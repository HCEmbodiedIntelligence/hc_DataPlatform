import type {
  ManualIssue,
  ManualIssueSeverity,
  ManualIssueType,
} from "../../../entities/manual-issue";
import {
  asCleaningDraftId,
  type CleaningDraftId,
} from "../../../entities/cleaning-draft";
import { asManualIssueId } from "../../../entities/manual-issue";
import { request } from "../../../shared/api/http-client";
import { adaptManualIssueEnvelope } from "./manual-issues.adapter";
import {
  createDraftFromIssueEnvelopeWireSchema,
  createManualIssueRequestWireSchema,
  resolveManualIssueRequestWireSchema,
  triageManualIssueRequestWireSchema,
} from "./manual-issues.schemas";
import { assertCleaningScope } from "./wire-common";

export interface ManualCleaningCommandRequest {
  readonly operationId: string;
  readonly method: "POST" | "PUT" | "DELETE";
  readonly path: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body?: unknown;
  readonly signal?: AbortSignal;
}

export type ManualCleaningCommandTransport = (
  request: ManualCleaningCommandRequest,
) => Promise<unknown>;

const defaultTransport: ManualCleaningCommandTransport = (command) =>
  request({
    method: command.method,
    path: command.path,
    ...(command.body === undefined ? {} : { body: command.body }),
    ...(command.headers["Idempotency-Key"]
      ? { idempotencyKey: command.headers["Idempotency-Key"] }
      : {}),
    ...(command.headers["If-Match"]
      ? { ifMatch: command.headers["If-Match"] }
      : {}),
    ...(command.signal ? { signal: command.signal } : {}),
  });

let configuredTransport: ManualCleaningCommandTransport = defaultTransport;

export function configureManualCleaningCommandTransport(
  transport: ManualCleaningCommandTransport,
): () => void {
  configuredTransport = transport;
  return () => {
    if (configuredTransport === transport)
      configuredTransport = defaultTransport;
  };
}

export interface CreateManualIssueCommandInput {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
  /** Used for caller context verification only; the backend derives it and it is omitted from the wire body. */
  readonly datasetId: string;
  readonly versionId: string;
  readonly episodeId: string;
  readonly revisionId: string;
  readonly streamId: string;
  readonly startNs: string;
  readonly endNs: string;
  readonly issueType: ManualIssueType;
  readonly severity: ManualIssueSeverity;
  readonly note: string;
  readonly idempotencyKey: string;
  readonly signal?: AbortSignal;
}

export async function createManualIssueCommand(
  input: CreateManualIssueCommandInput,
  transport: ManualCleaningCommandTransport = configuredTransport,
): Promise<ManualIssue> {
  if (!input.datasetId.trim()) throw new TypeError("datasetId is required");
  const body = createManualIssueRequestWireSchema.parse({
    origin_dataset_version_id: input.versionId,
    episode_id: input.episodeId,
    episode_revision_id: input.revisionId,
    episode_stream_id: input.streamId,
    start_ns: input.startNs,
    end_ns: input.endNs,
    issue_type: input.issueType,
    severity: input.severity,
    note: input.note,
  });
  const raw = await transport({
    operationId: "createManualIssue",
    method: "POST",
    path: `/projects/${encodeURIComponent(input.projectId)}/regions/${encodeURIComponent(input.regionCode)}/manual-issues`,
    headers: {
      "X-Organization-Id": input.organizationId,
      "X-Project-Id": input.projectId,
      "X-Region-Code": input.regionCode,
      "Idempotency-Key": input.idempotencyKey,
    },
    body,
    ...(input.signal ? { signal: input.signal } : {}),
  });
  return adaptManualIssueEnvelope(raw, input);
}

export interface CreateAnnotationManualIssueCommandInput {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
  readonly annotationTaskId: string;
  readonly streamRef: string;
  readonly relativeStartNs: string;
  readonly relativeEndNs: string;
  readonly discoverySource: "ANNOTATOR" | "REVIEWER";
  readonly issueType: ManualIssueType;
  readonly severity: ManualIssueSeverity;
  readonly note: string;
  readonly idempotencyKey: string;
  readonly signal?: AbortSignal;
}

/**
 * Creates a ManualIssue from an annotation task without asking the browser to
 * invent Dataset Version / Episode / Revision identities. The backend resolves
 * and validates those immutable facts from the task and selected stream.
 */
export async function createAnnotationManualIssueCommand(
  input: CreateAnnotationManualIssueCommandInput,
  transport: ManualCleaningCommandTransport = configuredTransport,
): Promise<ManualIssue> {
  const body = createManualIssueRequestWireSchema.parse({
    source_kind: "ANNOTATION_TASK",
    discovery_source: input.discoverySource,
    annotation_task_id: input.annotationTaskId,
    stream_ref: input.streamRef,
    relative_start_ns: input.relativeStartNs,
    relative_end_ns: input.relativeEndNs,
    issue_type: input.issueType,
    severity: input.severity,
    note: input.note,
  });
  const raw = await transport({
    operationId: "createManualIssue",
    method: "POST",
    path: `/projects/${encodeURIComponent(input.projectId)}/regions/${encodeURIComponent(input.regionCode)}/manual-issues`,
    headers: {
      "X-Organization-Id": input.organizationId,
      "X-Project-Id": input.projectId,
      "X-Region-Code": input.regionCode,
      "Idempotency-Key": input.idempotencyKey,
    },
    body,
    ...(input.signal ? { signal: input.signal } : {}),
  });
  return adaptManualIssueEnvelope(raw, input);
}

interface ExistingManualIssueCommandInput {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
  readonly manualIssueId: string;
  /** Strong server ETag used as If-Match; never an inferred client counter. */
  readonly expectedVersion: string;
  readonly idempotencyKey: string;
  readonly signal?: AbortSignal;
}

export interface TriageManualIssueCommandInput
  extends ExistingManualIssueCommandInput {
  readonly targetStatus: "OPEN" | "IN_PROGRESS";
  readonly severity: ManualIssueSeverity;
  readonly assigneeId: string | null;
  readonly reason: string;
}

export interface ResolveManualIssueCommandInput
  extends ExistingManualIssueCommandInput {
  readonly resolutionVersionId: string;
  readonly resolutionNote: string;
}

export type CreateDraftFromManualIssueCommandInput =
  ExistingManualIssueCommandInput;

export type CreateDraftFromManualIssueResult =
  | {
      readonly disposition: "CREATED" | "ALREADY_LINKED";
      readonly draftId: CleaningDraftId;
      readonly context: {
        readonly schemaVersion: 1;
        readonly datasetId: string;
        readonly baseVersionId: string;
        readonly episodeId: string;
        readonly baseRevisionId: string;
        readonly selectedStreamId: string;
        readonly selectedChannelPath: string | null;
        readonly startNs: string;
        readonly endNs: string;
        readonly manualIssueIds: readonly [ReturnType<typeof asManualIssueId>];
      };
    }
  | {
      readonly disposition: "SELECTION_REQUIRED";
      readonly selectionToken: string;
      readonly expiresAt: string;
      readonly candidates: readonly {
        readonly draftId: CleaningDraftId;
        readonly baseVersionId: string;
        readonly baseRevisionId: string;
        readonly manualIssueIds: readonly [ReturnType<typeof asManualIssueId>];
        readonly updatedAt: string;
      }[];
    };

function issueCommandPath(
  input: ExistingManualIssueCommandInput,
  suffix = "",
): string {
  asManualIssueId(input.manualIssueId);
  return `/projects/${encodeURIComponent(input.projectId)}/regions/${encodeURIComponent(input.regionCode)}/manual-issues/${encodeURIComponent(input.manualIssueId)}${suffix}`;
}

function issueCommandHeaders(
  input: ExistingManualIssueCommandInput,
): Readonly<Record<string, string>> {
  return {
    "X-Organization-Id": input.organizationId,
    "X-Project-Id": input.projectId,
    "X-Region-Code": input.regionCode,
    "Idempotency-Key": input.idempotencyKey,
    "If-Match": input.expectedVersion,
  };
}

export async function triageManualIssueCommand(
  input: TriageManualIssueCommandInput,
  transport: ManualCleaningCommandTransport = configuredTransport,
): Promise<ManualIssue> {
  const body = triageManualIssueRequestWireSchema.parse({
    target_status: input.targetStatus,
    severity: input.severity,
    assignee_id: input.assigneeId,
    reason: input.reason,
  });
  const raw = await transport({
    operationId: "triageManualIssue",
    method: "POST",
    path: issueCommandPath(input, ":triage"),
    headers: issueCommandHeaders(input),
    body,
    ...(input.signal ? { signal: input.signal } : {}),
  });
  return adaptManualIssueEnvelope(raw, input);
}

export async function resolveManualIssueCommand(
  input: ResolveManualIssueCommandInput,
  transport: ManualCleaningCommandTransport = configuredTransport,
): Promise<ManualIssue> {
  const body = resolveManualIssueRequestWireSchema.parse({
    resolution_version_id: input.resolutionVersionId,
    resolution_note: input.resolutionNote,
  });
  const raw = await transport({
    operationId: "resolveManualIssue",
    method: "POST",
    path: issueCommandPath(input, ":resolve"),
    headers: issueCommandHeaders(input),
    body,
    ...(input.signal ? { signal: input.signal } : {}),
  });
  return adaptManualIssueEnvelope(raw, input);
}

/**
 * P09-only Issue -> Draft command. This is deliberately not shared with the
 * P07 ReviewFinding -> successor transaction. The initial body is exactly `{}`;
 * source Version/Revision/Stream/range are never recomputed or submitted here.
 */
export async function createCleaningDraftFromManualIssueCommand(
  input: CreateDraftFromManualIssueCommandInput,
  transport: ManualCleaningCommandTransport = configuredTransport,
): Promise<CreateDraftFromManualIssueResult> {
  const body = {};
  const raw = await transport({
    operationId: "createCleaningDraftFromManualIssue",
    method: "POST",
    path: issueCommandPath(input, "/cleaning-drafts"),
    headers: issueCommandHeaders(input),
    body,
    ...(input.signal ? { signal: input.signal } : {}),
  });
  const wire = createDraftFromIssueEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, input);
  if (wire.data.disposition === "SELECTION_REQUIRED") {
    return {
      disposition: "SELECTION_REQUIRED",
      selectionToken: wire.data.selection_token,
      expiresAt: wire.data.expires_at,
      candidates: wire.data.candidates.map((candidate) => ({
        draftId: asCleaningDraftId(candidate.draft_id),
        baseVersionId: candidate.base_version_id,
        baseRevisionId: candidate.base_revision_id,
        manualIssueIds: [asManualIssueId(candidate.manual_issue_ids[0])],
        updatedAt: candidate.updated_at,
      })),
    };
  }
  return {
    disposition: wire.data.disposition,
    draftId: asCleaningDraftId(wire.data.draft_id),
    context: {
      schemaVersion: 1,
      datasetId: wire.data.context.dataset_id,
      baseVersionId: wire.data.context.base_version_id,
      episodeId: wire.data.context.episode_id,
      baseRevisionId: wire.data.context.base_revision_id,
      selectedStreamId: wire.data.context.selected_stream_id,
      selectedChannelPath: wire.data.context.selected_channel_path,
      startNs: wire.data.context.start_ns,
      endNs: wire.data.context.end_ns,
      manualIssueIds: [asManualIssueId(wire.data.context.manual_issue_ids[0])],
    },
  };
}
