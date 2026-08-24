import { delay, http, HttpResponse } from 'msw';
import {
  cleaningDrafts,
  cleaningEdl,
  cleaningFixtureIds,
  cleaningFixtureScope,
  cleaningManualIssues,
  cleaningOperationHash,
  makeAsyncJob,
  makeCleaningBootstrap,
  makeCleaningDraftDetail,
  makeCleaningDraftListEnvelope,
  makeCleaningDraftSummaryEnvelope,
  makeCleaningEventsEnvelope,
  makeCreateDraftEnvelope,
  makeManualIssueEnvelope,
  makeManualIssueListEnvelope,
  makeManualIssuePageEnvelope,
  makeReturnedBootstrap,
  makeReviewFindingsEnvelope,
  previewReady,
} from '../fixtures/cleaning';
import { getCleaningScenario } from '../scenarios/cleaning';

const issueRoot = '*/api/v1/projects/:projectId/regions/:regionCode/manual-issues';
const draftRoot = '*/api/v1/projects/:projectId/regions/:regionCode/cleaning-drafts';
const at = '2026-08-06T13:20:00Z';
let rateLimited = false;
let offlineFailed = false;

function problem(status: number, code: string, message: string, blockedReasons: readonly unknown[] = []) {
  return HttpResponse.json({ error: { code, message, field_errors: [], operation_errors: [], blocked_reasons: blockedReasons, request_id: `req_cleaning_${code.toLowerCase()}`, retryable: status === 429 || status >= 500 } }, { status });
}

function validScope(params: Readonly<Record<string, string | readonly string[] | undefined>>): Response | null {
  return params.projectId === cleaningFixtureScope.project_id && params.regionCode === cleaningFixtureScope.region_code
    ? null
    : problem(404, 'NOT_FOUND', 'Fixture scope 不存在');
}

function readGuard(request: Request, params: Readonly<Record<string, string | readonly string[] | undefined>>): Response | null {
  return validScope(params) ?? (!request.headers.get('X-Client-Version') ? problem(400, 'MISSING_HEADER', '缺少 X-Client-Version') : null);
}

function writeGuard(
  request: Request,
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
  requireIfMatch = true,
): Response | null {
  return readGuard(request, params)
    ?? (!request.headers.get('Idempotency-Key') && request.method !== 'PUT' ? problem(400, 'IDEMPOTENCY_KEY_REQUIRED', '缺少 Idempotency-Key') : null)
    ?? (requireIfMatch && !request.headers.get('If-Match') ? problem(428, 'PRECONDITION_REQUIRED', '缺少 If-Match') : null);
}

async function scenarioGate(kind: 'list' | 'detail' | 'summary' | 'bootstrap'): Promise<Response | null> {
  const scenario = getCleaningScenario();
  if (scenario === 'first-loading') await delay(2_000);
  if (scenario === 'forbidden') return problem(403, 'FORBIDDEN', 'cleaning.read 已撤销');
  if (scenario === 'fatal-error' && (kind === 'list' || kind === 'bootstrap')) return problem(500, 'SERVER_ERROR', 'Fixture 首屏失败');
  if (scenario === 'partial-error' && kind === 'summary') return problem(500, 'SUMMARY_UNAVAILABLE', '摘要暂不可用');
  if (scenario === 'not-found' && (kind === 'detail' || kind === 'bootstrap')) return problem(404, 'NOT_FOUND', 'Draft 不存在');
  if (scenario === 'gone' && (kind === 'detail' || kind === 'bootstrap')) return problem(410, 'GONE', 'Draft 已归档');
  if (scenario === 'rate-limited' && !rateLimited) { rateLimited = true; return problem(429, 'RATE_LIMITED', '请稍后重试'); }
  if (scenario === 'offline-recovery' && !offlineFailed) { offlineFailed = true; return HttpResponse.error(); }
  return null;
}

function issueById(id: string) {
  return Object.values(cleaningManualIssues).find((issue) => issue.id === id);
}

function detailForScenario(draftId: string) {
  if (getCleaningScenario() === 'returned' || getCleaningScenario() === 'returned-successor') {
    return draftId === cleaningFixtureIds.draftSuccessor
      ? makeCleaningDraftDetail(draftId)
      : { ...makeCleaningDraftDetail('returned'), data: { ...makeCleaningDraftDetail('returned').data, draft: cleaningDrafts.returned, origin: cleaningDrafts.returned.origin } };
  }
  return makeCleaningDraftDetail(draftId);
}

const handlers = [
  http.post(issueRoot, async ({ request, params }) => {
    const invalid = writeGuard(request, params, false); if (invalid) return invalid;
    const body = await request.json() as Record<string, unknown>;
    const fields = [
      'origin_dataset_version_id', 'episode_id', 'episode_revision_id', 'episode_stream_id',
      'start_ns', 'end_ns', 'issue_type', 'severity', 'note',
    ];
    if (fields.some((field) => typeof body[field] !== 'string')) {
      return problem(422, 'VALIDATION_FAILED', 'ManualIssue 创建字段无效');
    }
    if (!/^(0|[1-9][0-9]*)$/.test(String(body.start_ns))
      || !/^(0|[1-9][0-9]*)$/.test(String(body.end_ns))
      || BigInt(String(body.start_ns)) >= BigInt(String(body.end_ns))) {
      return problem(422, 'VALIDATION_FAILED', 'ManualIssue 范围无效');
    }
    return HttpResponse.json(makeManualIssueEnvelope({
      ...cleaningManualIssues.open,
      id: 'issue_fx_mc_created_01',
      etag: '"issue_fx_mc_created_01:v1"',
      origin_dataset_version_id: body.origin_dataset_version_id,
      episode_id: body.episode_id,
      episode_revision_id: body.episode_revision_id,
      episode_stream_id: body.episode_stream_id,
      start_ns: body.start_ns,
      end_ns: body.end_ns,
      issue_type: body.issue_type,
      severity: body.severity,
      note: body.note,
      status: 'OPEN',
      assignee: null,
      related_drafts: [],
      resolution_version: null,
      resolution_note: null,
      resolved_at: null,
      resolved_by: null,
      allowed_actions: ['VIEW_EPISODE', 'TRIAGE', 'CREATE_DRAFT'],
      blocked_reasons: [],
    }), { status: 201 });
  }),
  http.get(`${issueRoot}:page`, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('summary'); if (gate) return gate;
    const scenario = getCleaningScenario();
    return HttpResponse.json(makeManualIssuePageEnvelope(scenario === 'empty' || scenario === 'filtered-empty' ? [] : undefined));
  }),
  http.get(issueRoot, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('list'); if (gate) return gate;
    const url = new URL(request.url);
    if (url.searchParams.has('after') && url.searchParams.has('before')) return problem(400, 'INVALID_CURSOR', '游标互斥');
    const scenario = getCleaningScenario();
    if (scenario === 'contract-mismatch') return HttpResponse.json({ ...makeManualIssueListEnvelope(), scope: { ...cleaningFixtureScope, project_id: 'wrong_project' } });
    if (scenario === 'unknown-enum') return HttpResponse.json(makeManualIssueListEnvelope([{ ...cleaningManualIssues.open, status: 'FUTURE_STATE', allowed_actions: [] }]));
    if (scenario === 'empty' || scenario === 'filtered-empty' || url.searchParams.get('q') === 'no-match') return HttpResponse.json(makeManualIssueListEnvelope([]));
    return HttpResponse.json(makeManualIssueListEnvelope());
  }),
  http.get(`${issueRoot}/:issueId`, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('detail'); if (gate) return gate;
    const issue = issueById(String(params.issueId));
    return issue ? HttpResponse.json(makeManualIssueEnvelope(issue)) : problem(404, 'NOT_FOUND', 'ManualIssue 不存在');
  }),
  http.post(`${issueRoot}/:issueId\\:triage`, async ({ request, params }) => {
    const invalid = writeGuard(request, params); if (invalid) return invalid;
    if (getCleaningScenario() === 'conflict') return problem(412, 'PRECONDITION_FAILED', 'Issue ETag 已变化');
    const source = issueById(String(params.issueId)); if (!source) return problem(404, 'NOT_FOUND', 'ManualIssue 不存在');
    const body = await request.json() as Record<string, unknown>;
    if (!['OPEN', 'IN_PROGRESS'].includes(String(body.target_status)) || typeof body.reason !== 'string' || !body.reason.trim() || (body.assignee_id != null && typeof body.assignee_id !== 'string')) return problem(422, 'VALIDATION_FAILED', '分诊字段无效');
    const assigneeId = typeof body.assignee_id === 'string' ? body.assignee_id : null;
    const next = { ...source, etag: `"${source.id}:v-next"`, status: body.target_status, severity: body.severity, assignee: assigneeId ? actorFromId(assigneeId) : null, updated_at: '2026-08-06T13:25:00Z' };
    return HttpResponse.json(makeManualIssueEnvelope(next));
  }),
  http.post(`${issueRoot}/:issueId/cleaning-drafts`, async ({ request, params }) => {
    const invalid = writeGuard(request, params); if (invalid) return invalid;
    const body = await request.json();
    if (JSON.stringify(body) !== '{}') return problem(422, 'BROWSER_CONTEXT_FORBIDDEN', 'Issue→Draft body 必须为空');
    return HttpResponse.json(makeCreateDraftEnvelope());
  }),
  http.post(`${issueRoot}/:issueId\\:resolve`, async ({ request, params }) => {
    const invalid = writeGuard(request, params); if (invalid) return invalid;
    const source = issueById(String(params.issueId)); if (!source) return problem(404, 'NOT_FOUND', 'ManualIssue 不存在');
    const body = await request.json() as Record<string, unknown>;
    if (typeof body.resolution_version_id !== 'string' || typeof body.resolution_note !== 'string') return problem(422, 'VALIDATION_FAILED', '解决字段无效');
    return HttpResponse.json(makeManualIssueEnvelope(cleaningManualIssues.resolved));
  }),
  http.delete(`${issueRoot}/:issueId`, () => problem(405, 'OPERATION_NOT_ALLOWED', 'ManualIssue 不支持删除')),

  http.get(`${draftRoot}:summary`, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('summary'); if (gate) return gate;
    return HttpResponse.json(makeCleaningDraftSummaryEnvelope());
  }),
  http.get(draftRoot, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('list'); if (gate) return gate;
    const url = new URL(request.url);
    if (url.searchParams.has('after') && url.searchParams.has('before')) return problem(400, 'INVALID_CURSOR', '游标互斥');
    const scenario = getCleaningScenario();
    if (scenario === 'contract-mismatch') return HttpResponse.json({ ...makeCleaningDraftListEnvelope(), query_signature: 9 });
    if (scenario === 'unknown-enum') return HttpResponse.json(makeCleaningDraftListEnvelope([{ ...cleaningDrafts.editing, status: 'FUTURE_STATE', allowed_actions: [] }]));
    if (scenario === 'empty' || scenario === 'filtered-empty' || url.searchParams.get('q') === 'no-match') return HttpResponse.json(makeCleaningDraftListEnvelope([]));
    if (scenario === 'returned' || scenario === 'returned-successor') return HttpResponse.json(makeCleaningDraftListEnvelope([cleaningDrafts.returned, cleaningDrafts.successor]));
    return HttpResponse.json(makeCleaningDraftListEnvelope());
  }),
  http.get(`${draftRoot}/:draftId/summary`, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('detail'); if (gate) return gate;
    return HttpResponse.json(detailForScenario(String(params.draftId)));
  }),
  http.get(`${draftRoot}/:draftId/events`, ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    if (new URL(request.url).searchParams.get('limit') !== '10') return problem(400, 'INVALID_LIMIT', '事件 limit 必须为 10');
    return HttpResponse.json(makeCleaningEventsEnvelope());
  }),
  http.get(`${draftRoot}/:draftId/bootstrap`, async ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    const gate = await scenarioGate('bootstrap'); if (gate) return gate;
    const draftId = String(params.draftId);
    const scenario = getCleaningScenario();
    if (scenario === 'contract-mismatch') return HttpResponse.json({ ...makeCleaningBootstrap(draftId), data: { ...makeCleaningBootstrap(draftId).data, origin: reviewOriginConflict() } });
    if ((scenario === 'returned' || scenario === 'returned-successor') && draftId === cleaningFixtureIds.draftIssue) return HttpResponse.json(makeReturnedBootstrap());
    return HttpResponse.json(makeCleaningBootstrap(draftId));
  }),
  http.put(`${draftRoot}/:draftId/edl`, async ({ request, params }) => {
    const invalid = writeGuard(request, params); if (invalid) return invalid;
    if (getCleaningScenario() === 'save-conflict' || getCleaningScenario() === 'conflict') return problem(412, 'PRECONDITION_FAILED', 'EDL 基线已变化');
    if (getCleaningScenario() === 'save-validation-error') return problem(422, 'EDL_VALIDATION_FAILED', 'EDL 校验失败');
    const body = await request.json() as Record<string, unknown>;
    if (!Array.isArray(body.operations) || typeof body.expected_edl_revision !== 'string' || typeof body.client_mutation_id !== 'string') return problem(422, 'EDL_VALIDATION_FAILED', 'EDL 请求无效');
    return HttpResponse.json({ data: { draft: cleaningDrafts.editing, edl: { ...cleaningEdl, operations: body.operations }, request_id: 'req_fx_mc_save_01' }, scope: cleaningFixtureScope, request_id: 'req_fx_mc_save_01', contract_version: 'manual-cleaning.v1' });
  }),
  http.post(`${draftRoot}/:draftId/previews`, async ({ request, params }) => {
    const invalid = writeGuard(request, params); if (invalid) return invalid;
    const body = await request.json() as Record<string, unknown>;
    if (body.operation_hash !== cleaningOperationHash) return problem(409, 'OPERATION_HASH_MISMATCH', 'Preview hash 已陈旧');
    const queued = { preview_id: cleaningFixtureIds.preview, draft_id: String(params.draftId), base_revision_id: String(body.base_revision_id), edl_revision: String(body.edl_revision), operation_hash: String(body.operation_hash), job_id: cleaningFixtureIds.previewJob, created_at: at, status: 'QUEUED', expires_at: null };
    return HttpResponse.json({ preview: queued, job: makeAsyncJob('CLEANING_PREVIEW', String(params.draftId)), scope: cleaningFixtureScope, request_id: 'req_fx_mc_preview_01', contract_version: 'manual-cleaning.v1' }, { status: 202 });
  }),
  http.post(`${draftRoot}/:draftId/commits`, async ({ request, params }) => {
    const invalid = writeGuard(request, params); if (invalid) return invalid;
    if (getCleaningScenario() === 'commit-blocked') return problem(422, 'PREVIEW_REQUIRED', 'Preview 不可提交', [{ code: 'PREVIEW_STALE', message: 'Preview 与 EDL hash 不一致' }]);
    const body = await request.json() as Record<string, unknown>;
    if (body.preview_id !== previewReady.preview_id || body.operation_hash !== cleaningOperationHash) return problem(422, 'PREVIEW_STALE', 'Preview 不匹配');
    const queued = { commit_id: cleaningFixtureIds.commit, draft_id: String(params.draftId), preview_id: String(body.preview_id), job_id: cleaningFixtureIds.commitJob, created_at: at, successor_composition_hash: body.successor_composition_hash ?? null, status: 'QUEUED', output_revisions: [], output_version: null, materialization_status: 'NOT_STARTED' };
    return HttpResponse.json({ commit: queued, job: makeAsyncJob('CLEANING_COMMIT', String(params.draftId)), scope: cleaningFixtureScope, request_id: 'req_fx_mc_commit_01', contract_version: 'manual-cleaning.v1' }, { status: 202 });
  }),
  http.get(`${draftRoot}/:draftId/review-findings`, ({ request, params }) => {
    const invalid = readGuard(request, params); if (invalid) return invalid;
    return HttpResponse.json(makeReviewFindingsEnvelope());
  }),
];

function actorFromId(id: string) { return { id, display_name: id === 'principal_fx_mc_processor_01' ? 'Fixture Processor' : id }; }
function reviewOriginConflict() { return { ...cleaningDrafts.editing.origin, review_return_lineage: cleaningDrafts.successor.origin.review_return_lineage }; }

export default handlers;
