import { describe, expect, it } from "vitest";
import { runtimeOperations } from "../../../shared/api/generated/platform-operations";
import {
  cleaningFixtureIds,
  cleaningFixtureScope,
  cleaningManualIssues,
  makeCreateDraftEnvelope,
  makeManualIssueEnvelope,
} from "../../../mocks/fixtures/cleaning";
import {
  createCleaningDraftFromManualIssueCommand,
  createAnnotationManualIssueCommand,
  createManualIssueCommand,
  resolveManualIssueCommand,
  triageManualIssueCommand,
  type ManualCleaningCommandRequest,
} from "./manual-issues.commands";

const scope = {
  organizationId: cleaningFixtureScope.organization_id,
  projectId: cleaningFixtureScope.project_id,
  regionCode: cleaningFixtureScope.region_code,
} as const;

function capture(response: unknown): {
  readonly calls: ManualCleaningCommandRequest[];
  readonly transport: (
    request: ManualCleaningCommandRequest,
  ) => Promise<unknown>;
} {
  const calls: ManualCleaningCommandRequest[] = [];
  return {
    calls,
    transport: async (request) => {
      calls.push(request);
      return response;
    },
  };
}

describe("P09 ManualIssue command ownership", () => {
  it("keeps browser requests source-bound and draft handoff source-free", async () => {
    const create = capture(makeManualIssueEnvelope());
    const created = await createManualIssueCommand(
      {
        ...scope,
        datasetId: cleaningFixtureIds.dataset,
        versionId: cleaningFixtureIds.baseVersion,
        episodeId: cleaningFixtureIds.episode,
        revisionId: cleaningFixtureIds.baseRevision,
        streamId: cleaningFixtureIds.stream,
        startNs: "1200000000",
        endNs: "4200000000",
        issueType: "POSE_JITTER",
        severity: "HIGH",
        note: "Source-bound manual issue.",
        idempotencyKey: "p09-create-command-test",
      },
      create.transport,
    );
    expect(created.id).toBe(cleaningFixtureIds.issueOpen);
    expect(create.calls).toEqual([
      expect.objectContaining({
        operationId: "createManualIssue",
        method: "POST",
        path: `/projects/${scope.projectId}/regions/${scope.regionCode}/manual-issues`,
        headers: expect.objectContaining({
          "Idempotency-Key": "p09-create-command-test",
        }),
        body: {
          source_kind: "DATASET",
          discovery_source: "DATA_VIEWER",
          origin_dataset_version_id: cleaningFixtureIds.baseVersion,
          episode_id: cleaningFixtureIds.episode,
          episode_revision_id: cleaningFixtureIds.baseRevision,
          episode_stream_id: cleaningFixtureIds.stream,
          start_ns: "1200000000",
          end_ns: "4200000000",
          issue_type: "POSE_JITTER",
          severity: "HIGH",
          note: "Source-bound manual issue.",
        },
      }),
    ]);
    expect(
      (create.calls[0]?.body as Record<string, unknown>).dataset_id,
    ).toBeUndefined();
    expect(create.calls[0]?.headers["If-Match"]).toBeUndefined();

    const annotationReport = capture(
      makeManualIssueEnvelope({
        ...cleaningManualIssues.open,
        discovery_source: "ANNOTATOR",
        annotation_task_id: "annotation_task_fx_01",
      }),
    );
    const reported = await createAnnotationManualIssueCommand(
      {
        ...scope,
        annotationTaskId: "annotation_task_fx_01",
        streamRef: "/camera/front",
        relativeStartNs: "100000000",
        relativeEndNs: "400000000",
        discoverySource: "ANNOTATOR",
        issueType: "MISSING_FRAME",
        severity: "HIGH",
        note: "Automatic QC missed this frame gap.",
        idempotencyKey: "p08-report-data-issue",
      },
      annotationReport.transport,
    );
    expect(reported.discoverySource).toBe("ANNOTATOR");
    expect(annotationReport.calls[0]).toMatchObject({
      operationId: "createManualIssue",
      body: {
        source_kind: "ANNOTATION_TASK",
        discovery_source: "ANNOTATOR",
        annotation_task_id: "annotation_task_fx_01",
        stream_ref: "/camera/front",
        relative_start_ns: "100000000",
        relative_end_ns: "400000000",
        issue_type: "MISSING_FRAME",
        severity: "HIGH",
        note: "Automatic QC missed this frame gap.",
      },
    });

    const draft = capture(makeCreateDraftEnvelope());
    const draftResult = await createCleaningDraftFromManualIssueCommand(
      {
        ...scope,
        manualIssueId: cleaningFixtureIds.issueOpen,
        expectedVersion: cleaningManualIssues.open.etag,
        idempotencyKey: "p09-draft-command-test",
      },
      draft.transport,
    );
    expect(draftResult).toMatchObject({
      disposition: "ALREADY_LINKED",
      draftId: cleaningFixtureIds.draftIssue,
    });
    expect(draft.calls).toEqual([
      expect.objectContaining({
        operationId: "createCleaningDraftFromManualIssue",
        path: `/projects/${scope.projectId}/regions/${scope.regionCode}/manual-issues/${cleaningFixtureIds.issueOpen}/cleaning-drafts`,
        headers: expect.objectContaining({
          "If-Match": cleaningManualIssues.open.etag,
          "Idempotency-Key": "p09-draft-command-test",
        }),
        body: {},
      }),
    ]);
  });

  it("uses the dedicated P09 triage and durable-resolution routes", async () => {
    const triage = capture(
      makeManualIssueEnvelope({
        ...cleaningManualIssues.open,
        status: "IN_PROGRESS",
        severity: "CRITICAL",
        assignee: {
          id: "principal_p09_assignee",
          display_name: "P09 Assignee",
        },
      }),
    );
    await triageManualIssueCommand(
      {
        ...scope,
        manualIssueId: cleaningFixtureIds.issueOpen,
        expectedVersion: cleaningManualIssues.open.etag,
        idempotencyKey: "p09-triage-command-test",
        targetStatus: "IN_PROGRESS",
        severity: "CRITICAL",
        assigneeId: "principal_p09_assignee",
        reason: "Triage was confirmed by an operator.",
      },
      triage.transport,
    );
    expect(triage.calls[0]).toMatchObject({
      operationId: "triageManualIssue",
      path: `/projects/${scope.projectId}/regions/${scope.regionCode}/manual-issues/${cleaningFixtureIds.issueOpen}:triage`,
      body: {
        target_status: "IN_PROGRESS",
        severity: "CRITICAL",
        assignee_id: "principal_p09_assignee",
        reason: "Triage was confirmed by an operator.",
      },
    });

    const resolve = capture(
      makeManualIssueEnvelope(cleaningManualIssues.resolved),
    );
    await resolveManualIssueCommand(
      {
        ...scope,
        manualIssueId: cleaningFixtureIds.issueResolved,
        expectedVersion: cleaningManualIssues.resolved.etag,
        idempotencyKey: "p09-resolve-command-test",
        resolutionVersionId: cleaningFixtureIds.outputVersion,
        resolutionNote: "The durable output lineage is READY.",
      },
      resolve.transport,
    );
    expect(resolve.calls[0]).toMatchObject({
      operationId: "resolveManualIssue",
      path: `/projects/${scope.projectId}/regions/${scope.regionCode}/manual-issues/${cleaningFixtureIds.issueResolved}:resolve`,
      body: {
        resolution_version_id: cleaningFixtureIds.outputVersion,
        resolution_note: "The durable output lineage is READY.",
      },
    });
  });

  it("keeps exactly the real P09 operations in the generated runtime gate", () => {
    const p09 = runtimeOperations.filter((operation) =>
      operation.path.includes("/manual-issues"),
    );
    expect(p09).toEqual([
      {
        method: "GET",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues",
      },
      {
        method: "POST",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues",
      },
      {
        method: "GET",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues:page",
      },
      {
        method: "GET",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}",
      },
      {
        method: "POST",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}:resolve",
      },
      {
        method: "POST",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}:triage",
      },
      {
        method: "POST",
        path: "/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}/cleaning-drafts",
      },
    ]);
  });
});
