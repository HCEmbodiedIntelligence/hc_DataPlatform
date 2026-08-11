import { delay, http, HttpResponse } from 'msw';
import { getIngestScenario } from '../scenarios/ingest';
import {
  asyncJobFixture,
  dataSourceFixture,
  dataSourcePageFixture,
  ingestFixtureScope,
  internalUploadJobFixture,
  uploadBootstrapFixture,
  uploadCreationOptionsFixture,
  uploadEventPageFixture,
  uploadListFixture,
  uploadObjectPageFixture,
  uploadSessionFixture,
  uploadingSessionFixture,
  verificationRunFixture,
  verificationRunPageFixture,
} from '../fixtures/ingest';

const api = '*/api/v1/projects/:projectId/regions/:regionCode';
let rateLimitAttempts = 0;

function error(status: number, code: string, message: string, blockedReasons: readonly { code: string; message: string }[] = []) {
  return HttpResponse.json({ error: { code, message, field_errors: [], operation_errors: [], blocked_reasons: blockedReasons, request_id: `req_fx_${code.toLowerCase()}`, retryable: status >= 500 } }, { status });
}

function validateScope(params: Record<string, string | readonly string[] | undefined>) {
  return params.projectId === ingestFixtureScope.project_id && params.regionCode === ingestFixtureScope.region_code;
}

function validateRead(request: Request, params: Record<string, string | readonly string[] | undefined>): Response | null {
  if (!validateScope(params)) return error(404, 'NOT_FOUND', '资源不属于当前 Scope');
  if (!request.headers.get('X-Client-Version')) return error(400, 'MISSING_HEADER', '缺少 X-Client-Version');
  const url = new URL(request.url);
  if (url.searchParams.has('after') && url.searchParams.has('before')) return error(400, 'INVALID_CURSOR', 'after 与 before 互斥');
  return null;
}

function validateWrite(request: Request, params: Record<string, string | readonly string[] | undefined>, etag: boolean): Response | null {
  const read = validateRead(request, params);
  if (read) return read;
  if (!request.headers.get('Idempotency-Key')) return error(400, 'IDEMPOTENCY_KEY_REQUIRED', '写操作必须提供 Idempotency-Key');
  if (etag && !request.headers.get('If-Match')) return error(428, 'PRECONDITION_REQUIRED', '写操作必须提供 If-Match');
  return null;
}

async function scenarioDelay() {
  if (getIngestScenario() === 'first-loading') await delay(2_000);
  else if (getIngestScenario() === 'submitting') await delay(1_000);
}

function scenarioFailure(kind: 'list' | 'detail'): Response | null {
  const scenario = getIngestScenario();
  if (scenario === 'forbidden' || scenario === 'permission-revoked') return error(403, 'CAPABILITY_MISSING', '无权限');
  if (scenario === 'fatal-error' && kind === 'list') return error(500, 'INTERNAL_ERROR', 'Fixture 服务错误');
  if (scenario === 'not-found' && kind === 'detail') return error(404, 'NOT_FOUND', 'Fixture 资源不存在');
  if (scenario === 'gone' && kind === 'detail') return error(410, 'GONE', 'Fixture 资源已过期');
  if (scenario === 'rate-limited' && rateLimitAttempts++ === 0) return HttpResponse.json({ error: { code: 'RATE_LIMITED', message: '稍后重试', field_errors: [], operation_errors: [], blocked_reasons: [], request_id: 'req_fx_rate', retryable: true } }, { status: 429, headers: { 'Retry-After': '2' } });
  return null;
}

export const ingestHandlers = [
  http.get(`${api}/data-sources/page`, async ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    await scenarioDelay(); const failed = scenarioFailure('list'); if (failed) return failed;
    const scenario = getIngestScenario();
    if (scenario === 'contract-mismatch') return HttpResponse.json({ ...dataSourcePageFixture, access_key_secret: 'fixture-only-leak' });
    if (scenario === 'partial-error') return HttpResponse.json({ ...dataSourcePageFixture, component_errors: [{ component: 'summary', code: 'INTERNAL_ERROR', message: '摘要不可用', request_id: 'req_fx_partial' }] });
    if (scenario === 'empty' || scenario === 'filtered-empty') return HttpResponse.json({ ...dataSourcePageFixture, items: [], summary: scenario === 'empty' ? { ...dataSourcePageFixture.summary, total_count: '0', online_count: '0' } : dataSourcePageFixture.summary });
    if (scenario === 'unknown-enum') return HttpResponse.json({ ...dataSourcePageFixture, items: [{ ...dataSourcePageFixture.items[0], source_type: 'FUTURE_CONNECTOR', binding: { kind: 'UNKNOWN', raw: 'FUTURE_CONNECTOR', display_name: '未来连接器' } }] });
    return HttpResponse.json(dataSourcePageFixture);
  }),
  http.get(`${api}/data-sources/:sourceId`, ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    const failed = scenarioFailure('detail'); if (failed) return failed;
    if (getIngestScenario() === 'unknown-enum') return HttpResponse.json({ data: { ...dataSourceFixture, source_type: 'FUTURE_CONNECTOR', binding: { kind: 'UNKNOWN', raw: 'FUTURE_CONNECTOR', display_name: '未来连接器' }, configuration: { kind: 'UNKNOWN', raw_source_type: 'FUTURE_CONNECTOR', safe_projection: { display_name: '未来连接器', connector_family: null, migration_hint: null } }, allowed_actions: ['VIEW'] }, scope: ingestFixtureScope, request_id: 'req_fx_source_unknown', contract_version: 'ingest.v1alpha1' });
    return HttpResponse.json({ data: dataSourceFixture, scope: ingestFixtureScope, request_id: 'req_fx_source', contract_version: 'ingest.v1alpha1' });
  }),
  http.post(`${api}/data-sources`, async ({ request, params }) => {
    const invalid = validateWrite(request, params, false); if (invalid) return invalid;
    await scenarioDelay();
    if (getIngestScenario() === 'validation-error') return error(422, 'VALIDATION_ERROR', '字段校验失败');
    return HttpResponse.json({ data: dataSourceFixture, scope: ingestFixtureScope, request_id: 'req_fx_source_create', contract_version: 'ingest.v1alpha1' }, { status: 201 });
  }),
  http.patch(`${api}/data-sources/:sourceId`, async ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    if (getIngestScenario() === 'etag-conflict' || getIngestScenario() === 'conflict') return error(412, 'VERSION_CONFLICT', 'ETag 已变化');
    const body = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ data: { ...dataSourceFixture, name: typeof body.name === 'string' ? body.name : dataSourceFixture.name, etag: 'source-rv-10', config_version: '8' }, scope: ingestFixtureScope, request_id: 'req_fx_source_update', contract_version: 'ingest.v1alpha1' });
  }),
  http.post(`${api}/data-sources/:sourceId\\:rotate-credential`, ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    return HttpResponse.json({ data: { ...dataSourceFixture, credential: { ...dataSourceFixture.credential, masked_hint: 'token …B72C', version: '4' }, credential_version: '4', etag: 'source-rv-10' }, scope: ingestFixtureScope, request_id: 'req_fx_source_rotate', contract_version: 'ingest.v1alpha1' });
  }),
  http.post(`${api}/data-sources/:sourceId\\:test-connection`, ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    const connectionJob = { ...internalUploadJobFixture, id: 'job_connection_fx_01', type: 'DATA_SOURCE_CONNECTION_TEST', stage: 'CONNECTIVITY_CHECK', resource_ref: { resource_type: 'DATA_SOURCE', resource_id: 'source_fx_01' }, observed_versions: { config_version: '7', credential_version: '3' } };
    const job = { ...asyncJobFixture, job_id: 'job_connection_fx_01', job_type: 'DATA_SOURCE_CONNECTION_TEST', resource_type: 'DATA_SOURCE', resource_id: 'source_fx_01' };
    return HttpResponse.json({ data: connectionJob, job, scope: ingestFixtureScope, request_id: 'req_fx_connection', contract_version: 'ingest.v1alpha1' }, { status: 202, headers: { 'Cache-Control': 'private, no-store' } });
  }),
  ...(['enable', 'disable'] as const).map((operation) => http.post(`${api}/data-sources/:sourceId\\:${operation}`, ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    if (getIngestScenario() === 'etag-conflict' || getIngestScenario() === 'conflict') return error(412, 'VERSION_CONFLICT', 'ETag 已变化');
    return HttpResponse.json({ data: { ...dataSourceFixture, administrative_state: operation === 'enable' ? 'ENABLED' : 'DISABLED', etag: 'source-rv-10' }, scope: ingestFixtureScope, request_id: 'req_fx_source_state', contract_version: 'ingest.v1alpha1' });
  })),
  http.get(`${api}/upload-sessions`, async ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    await scenarioDelay(); const failed = scenarioFailure('list'); if (failed) return failed;
    const scenario = getIngestScenario();
    if (scenario === 'empty' || scenario === 'filtered-empty') return HttpResponse.json({ ...uploadListFixture, items: [] });
    if (scenario === 'contract-mismatch') return HttpResponse.json({ ...uploadListFixture, security_token: 'fixture-only-leak' });
    if (scenario === 'unknown-enum') return HttpResponse.json({ ...uploadListFixture, items: [{ ...uploadSessionFixture, lifecycle_status: 'FUTURE_TRANSFER' }] });
    return HttpResponse.json(uploadListFixture);
  }),
  http.get(`${api}/upload-sessions:creation-options`, ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    if (getIngestScenario() === 'preflight-blocked') return HttpResponse.json({ ...uploadCreationOptionsFixture, data: { ...uploadCreationOptionsFixture.data, allowed_actions: [], blocked_reasons: [{ code: 'POLICY_BLOCKED', message: '当前策略阻止创建上传。' }] } });
    return HttpResponse.json(uploadCreationOptionsFixture);
  }),
  http.post(`${api}/upload-sessions`, async ({ request, params }) => {
    const invalid = validateWrite(request, params, false); if (invalid) return invalid;
    await scenarioDelay();
    return HttpResponse.json({ data: { upload_session: uploadingSessionFixture, secret: { authorization: { authorization_id: 'auth_fx_01', issued_at: '2026-08-11T08:00:00Z', expires_at: '2026-08-11T08:15:00Z', refresh_after: '2026-08-11T08:10:00Z', oss_region: 'oss-cn-shanghai', endpoint: 'https://oss-cn-shanghai.example.test', bucket: 'fixture-upload-bucket', object_prefix: 'sessions/upload_fx_uploading/', credentials: { access_key_id: 'fixture-only-access-key', access_key_secret: 'fixture-only-secret', security_token: 'fixture-only-token' } }, policy: null, object_plans: [] } }, scope: ingestFixtureScope, request_id: 'req_fx_upload_create', contract_version: 'ingest.v1alpha1' }, { status: 201, headers: { 'Cache-Control': 'private, no-store' } });
  }),
  http.get(`${api}/upload-sessions/:uploadId/bootstrap`, async ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    await scenarioDelay(); const failed = scenarioFailure('detail'); if (failed) return failed;
    if (getIngestScenario() === 'contract-mismatch') return HttpResponse.json({ ...uploadBootstrapFixture, signed_url: 'https://fixture.invalid/leak' });
    return HttpResponse.json(uploadBootstrapFixture);
  }),
  http.get(`${api}/upload-sessions/:uploadId/objects`, ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    if (getIngestScenario() === 'partial-error') return error(500, 'INTERNAL_ERROR', '对象区域不可用');
    if (getIngestScenario() === 'empty') return HttpResponse.json({ ...uploadObjectPageFixture, items: [] });
    return HttpResponse.json(uploadObjectPageFixture);
  }),
  http.get(`${api}/upload-sessions/:uploadId/verification-runs`, ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    return HttpResponse.json(verificationRunPageFixture);
  }),
  http.get(`${api}/upload-sessions/:uploadId/events`, ({ request, params }) => {
    const invalid = validateRead(request, params); if (invalid) return invalid;
    if (getIngestScenario() === 'partial-error') return error(500, 'INTERNAL_ERROR', '事件区域不可用');
    return HttpResponse.json(getIngestScenario() === 'empty' ? { ...uploadEventPageFixture, items: [] } : uploadEventPageFixture);
  }),
  http.post(`${api}/upload-sessions/:uploadId\\:pause`, ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    if (getIngestScenario() === 'etag-conflict' || getIngestScenario() === 'conflict') return error(412, 'VERSION_CONFLICT', 'ETag 已变化');
    return HttpResponse.json({ data: { ...uploadSessionFixture, upload_id: String(params.uploadId), lifecycle_status: 'PAUSED', allowed_actions: ['RESUME', 'CANCEL'], etag: 'upload-rv-9', resource_version: '9' }, scope: ingestFixtureScope, request_id: 'req_fx_pause', contract_version: 'ingest.v1alpha1' });
  }),
  http.post(`${api}/upload-sessions/:uploadId\\:cancel`, ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    if (getIngestScenario() === 'etag-conflict' || getIngestScenario() === 'conflict') return error(412, 'VERSION_CONFLICT', 'ETag 已变化');
    const job = { ...asyncJobFixture, job_id: `job_cancel_${String(params.uploadId)}`, job_type: 'UPLOAD_CANCEL', resource_type: 'UPLOAD_SESSION', resource_id: String(params.uploadId) };
    const internal = { ...internalUploadJobFixture, id: job.job_id, type: 'UPLOAD_CANCEL', resource_ref: { resource_type: 'UPLOAD_SESSION', resource_id: String(params.uploadId) } };
    return HttpResponse.json({ data: internal, job, scope: ingestFixtureScope, request_id: 'req_fx_cancel', contract_version: 'ingest.v1alpha1' }, { status: 202 });
  }),
  http.post(`${api}/upload-sessions/:uploadId\\:retry-verification`, ({ request, params }) => {
    const invalid = validateWrite(request, params, true); if (invalid) return invalid;
    if (getIngestScenario() === 'etag-conflict' || getIngestScenario() === 'conflict') return error(412, 'VERSION_CONFLICT', 'ETag 已变化');
    const run = { ...verificationRunFixture, verification_run_id: 'verification_run_fx_retry', supersedes_run_id: verificationRunFixture.verification_run_id, status: 'QUEUED', resource_version: '6' };
    return HttpResponse.json({ data: { verification_run: run }, job: asyncJobFixture, scope: ingestFixtureScope, request_id: 'req_fx_retry', contract_version: 'ingest.v1alpha1' }, { status: 202 });
  }),
  http.get('*/api/v1/jobs/:jobId', ({ request, params }) => {
    if (!request.headers.get('X-Client-Version')) return error(400, 'MISSING_HEADER', '缺少 X-Client-Version');
    return HttpResponse.json({ ...asyncJobFixture, job_id: String(params.jobId) });
  }),
];

export function resetIngestHandlerState(): void { rateLimitAttempts = 0; }
export default ingestHandlers;
