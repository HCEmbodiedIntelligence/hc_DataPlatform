import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { asCleaningDraftId } from '../../src/entities/cleaning-draft';
import { asManualIssueId } from '../../src/entities/manual-issue';
import {
  adaptCleaningDraftDetailEnvelope,
  adaptCleaningDraftListEnvelope,
  adaptCleaningDraftSummaryEnvelope,
  adaptCleaningWorkbench,
  adaptManualIssueListEnvelope,
  cleaningDraftBootstrapEnvelopeWireSchema,
  cleaningDraftDetailEnvelopeWireSchema,
  cleaningDraftListEnvelopeWireSchema,
  cleaningDraftSummaryEnvelopeWireSchema,
  commitCleaningDraftRequestWireSchema,
  createCleaningDraftFromManualIssueCommand,
  type ManualCleaningCommandRequest,
  manualIssueListEnvelopeWireSchema,
  manualIssuesPageEnvelopeWireSchema,
  manualIssueKeys,
  readonlyReviewFindingKeys,
} from '../../src/features/cleaning/api';
import { sourceTimeToOutputs, timeMappingSegment } from '../../src/features/cleaning/time-mapping';
import { routes } from '../../src/features/cleaning/routing';
import {
  cleaningFixtureIds,
  cleaningFixtureScope,
  cleaningOperationHash,
  makeCleaningBootstrap,
  makeCleaningDraftDetail,
  makeCleaningDraftListEnvelope,
  makeCleaningDraftSummaryEnvelope,
  makeCreateDraftEnvelope,
  makeManualIssueListEnvelope,
  makeManualIssuePageEnvelope,
  makeReturnedBootstrap,
} from '../../src/mocks/fixtures/cleaning';
import { cleaningDraftsQueryCodec } from '../../src/pages/p10-cleaning-drafts/query-codec';
import { cleaningWorkbenchQueryCodec } from '../../src/pages/p11-manual-cleaning/query-codec';

const scope = {
  organizationId: cleaningFixtureScope.organization_id,
  projectId: cleaningFixtureScope.project_id,
  regionCode: cleaningFixtureScope.region_code,
};

describe('P09/P10/P11 manual-cleaning contract', () => {
  it('keeps ManualIssue and ReviewFinding isolated in code, identity and query-key domains', () => {
    const manualIssueSource = readFileSync('src/entities/manual-issue.ts', 'utf8');
    const reviewFindingSource = readFileSync('src/entities/review-finding.ts', 'utf8');
    expect(manualIssueSource).not.toMatch(/^import .*review-finding/m);
    expect(reviewFindingSource).not.toMatch(/^import .*manual-issue/m);
    expect(asManualIssueId(cleaningFixtureIds.issueOpen)).toBe(cleaningFixtureIds.issueOpen);
    expect(asCleaningDraftId(cleaningFixtureIds.draftIssue)).toBe(cleaningFixtureIds.draftIssue);
    expect(manualIssueKeys.detail(cleaningFixtureIds.issueOpen)[0]).toBe('manual-issues');
    expect(readonlyReviewFindingKeys.decision(cleaningFixtureIds.decision)[0]).toBe('review-findings');
    expect(manualIssueKeys.detail(cleaningFixtureIds.issueOpen)[0]).not.toBe(readonlyReviewFindingKeys.decision(cleaningFixtureIds.decision)[0]);
  });

  it('validates P09 fixtures through strict production schemas and adapters', () => {
    expect(manualIssuesPageEnvelopeWireSchema.parse(makeManualIssuePageEnvelope()).data.counts.total).toBe('3');
    const wire = manualIssueListEnvelopeWireSchema.parse(makeManualIssueListEnvelope());
    const page = adaptManualIssueListEnvelope(wire, scope);
    expect(page.items).toHaveLength(3);
    expect(page.items[0]?.source.startNs).toBe('1200000000');
    expect(page.items[1]?.status).toEqual({ kind: 'known', value: 'IN_PROGRESS' });
  });

  it('submits only the Issue identity, idempotency key and expected version for Issue → Draft', async () => {
    let descriptor: ManualCleaningCommandRequest | undefined;
    const result = await createCleaningDraftFromManualIssueCommand({
      ...scope,
      manualIssueId: cleaningFixtureIds.issueWork,
      expectedVersion: '"issue_fx_mc_work_01:v4"',
      idempotencyKey: 'idem_fx_issue_draft_01',
    }, (request) => {
      descriptor = request;
      return Promise.resolve(makeCreateDraftEnvelope());
    });
    expect(descriptor).toMatchObject({
      operationId: 'createCleaningDraftFromManualIssue',
      method: 'POST',
      body: {},
      headers: {
        'Idempotency-Key': 'idem_fx_issue_draft_01',
        'If-Match': '"issue_fx_mc_work_01:v4"',
      },
    });
    expect(Object.keys((descriptor as { body: object }).body)).toEqual([]);
    expect(result.disposition).toBe('ALREADY_LINKED');
    if (result.disposition !== 'SELECTION_REQUIRED') {
      expect(routes.cleaningWorkbench.build({ draftId: result.draftId })).toBe(`/manual/drafts/${result.draftId}`);
    }
  });

  it('roundtrips the three route codecs and rejects non-canonical compare/range inputs', () => {
    const p10 = cleaningDraftsQueryCodec.parse('scope=actionable&status=active&previewStatus=READY&previewStatus=READY&after=a&before=b&limit=100');
    expect(p10.scope).toBe('actionable');
    expect(p10.previewStatus).toEqual(['READY']);
    expect(p10.after).toBeUndefined();
    expect(p10.before).toBeUndefined();
    const canonicalP10 = cleaningDraftsQueryCodec.canonicalize(cleaningDraftsQueryCodec.build(p10).split('?')[1] ?? '');
    expect(cleaningDraftsQueryCodec.parse(canonicalP10.split('?')[1] ?? '')).toEqual(p10);

    const p11 = cleaningWorkbenchQueryCodec.parse('compare=preview&windowStartNs=9007199254740993&windowEndNs=9007199254740999');
    expect(p11.compare).toBe('source');
    expect(p11.windowStartNs).toBe('9007199254740993');
    const ab = cleaningWorkbenchQueryCodec.parse(cleaningWorkbenchQueryCodec.build(cleaningFixtureIds.draftIssue, { ...p11, compare: 'ab' }).split('?')[1] ?? '');
    expect(ab.compare).toBe('ab');
  });

  it('validates P10 list/summary/detail without loading EDL media in the list projection', () => {
    const listWire = cleaningDraftListEnvelopeWireSchema.parse(makeCleaningDraftListEnvelope());
    const list = adaptCleaningDraftListEnvelope(listWire, scope);
    expect(list.items.map((item) => item.id)).toContain(cleaningFixtureIds.draftSuccessor);
    expect(JSON.stringify(listWire.items)).not.toContain('viewer_manifest');
    const summaryWire = cleaningDraftSummaryEnvelopeWireSchema.parse(makeCleaningDraftSummaryEnvelope());
    expect(adaptCleaningDraftSummaryEnvelope(summaryWire, scope).scopeCounts.returned).toBe('1');
    const detailWire = cleaningDraftDetailEnvelopeWireSchema.parse(makeCleaningDraftDetail(cleaningFixtureIds.draftSuccessor));
    const detail = adaptCleaningDraftDetailEnvelope(detailWire, scope, cleaningFixtureIds.draftSuccessor);
    expect(detail.draft.origin.kind).toBe('REVIEW_RETURN');
    expect(detail.relationships.returnedFromReviewDecisionId).toBe(cleaningFixtureIds.decision);
  });

  it('validates both mutually-exclusive P11 origins and keeps review feedback readonly', () => {
    const issueWire = cleaningDraftBootstrapEnvelopeWireSchema.parse(makeCleaningBootstrap());
    const issueModel = adaptCleaningWorkbench(issueWire, scope, cleaningFixtureIds.draftIssue);
    expect(issueModel.draft.origin.kind).toBe('ISSUE_DERIVED');
    expect(issueModel.reviewFeedback).toBeNull();
    expect(issueModel.edl.operations).toHaveLength(2);

    const returnedWire = cleaningDraftBootstrapEnvelopeWireSchema.parse(makeReturnedBootstrap());
    const returnedModel = adaptCleaningWorkbench(returnedWire, scope, cleaningFixtureIds.draftIssue);
    expect(returnedModel.reviewFeedback?.findings).toHaveLength(2);
    expect(returnedModel.reviewFeedback?.findings.every((finding) => finding.immutable)).toBe(true);
    expect(returnedModel.allowedActions).not.toContain('SAVE_EDL');

    const conflict = makeCleaningBootstrap();
    const successorOrigin = makeCleaningBootstrap(cleaningFixtureIds.draftSuccessor).data.origin;
    expect(() => cleaningDraftBootstrapEnvelopeWireSchema.parse({ ...conflict, data: { ...conflict.data, origin: { ...conflict.data.origin, review_return_lineage: successorOrigin.review_return_lineage } } })).toThrow();
  });

  it('keeps half-open nanoseconds lossless and maps boundaries without Number()', () => {
    const map = {
      mappingVersion: '1',
      segments: [timeMappingSegment({
        sourceStartNs: '9007199254740993',
        sourceEndNs: '9007199254741993',
        outputRevisionId: cleaningFixtureIds.outputRevision,
        outputStartNs: '0',
        outputEndNs: '1000',
      })],
    } as const;
    expect(sourceTimeToOutputs(map, timeMappingSegment({ sourceStartNs: '9007199254740993', sourceEndNs: '9007199254741993', outputRevisionId: cleaningFixtureIds.outputRevision, outputStartNs: '0', outputEndNs: '1000' }).sourceStartNs)[0]?.outputNs).toBe('0');
    expect(sourceTimeToOutputs(map, timeMappingSegment({ sourceStartNs: '9007199254741992', sourceEndNs: '9007199254741993', outputRevisionId: cleaningFixtureIds.outputRevision, outputStartNs: '999', outputEndNs: '1000' }).sourceStartNs)[0]?.outputNs).toBe('999');
    expect(sourceTimeToOutputs(map, timeMappingSegment({ sourceStartNs: '9007199254741993', sourceEndNs: '9007199254741994', outputRevisionId: cleaningFixtureIds.outputRevision, outputStartNs: '1000', outputEndNs: '1001' }).sourceStartNs)).toEqual([]);
  });

  it('requires exact commit acknowledgement and rejects unknown browser-authored fields', () => {
    const input = {
      preview_id: cleaningFixtureIds.preview,
      base_revision_id: cleaningFixtureIds.baseRevision,
      edl_revision: '1',
      operation_hash: cleaningOperationHash,
      successor_composition_hash: null,
      acknowledgement: { reviewed_summary: true, compared_preview: true },
    } as const;
    expect(commitCleaningDraftRequestWireSchema.parse(input)).toEqual(input);
    expect(() => commitCleaningDraftRequestWireSchema.parse({ ...input, draft_id: 'browser_authored' })).toThrow();
    expect(() => commitCleaningDraftRequestWireSchema.parse({ ...input, acknowledgement: { reviewed_summary: true, compared_preview: false } })).toThrow();
  });
});
