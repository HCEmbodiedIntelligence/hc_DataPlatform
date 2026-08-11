import { delay, http, HttpResponse } from 'msw';
import {
  storageCostFixture,
  storageEmptyInventoryFixture,
  storageEmptyMultipartFixture,
  storageInventoryFixture,
  storageMultipartFixture,
  storageObjectDetailFixture,
  storageOverviewFixture,
  storageUnknownFixtures,
} from '../fixtures/storage-overview';
import { getStorageOverviewScenario, storageOfflineShouldFail } from '../scenarios/storage-overview';

const failure = (status: number, code: string, requestId: string) => HttpResponse.json({ error: { code, message: code, field_errors: [], operation_errors: [], blocked_reasons: [], request_id: requestId, retryable: status >= 500 } }, { status });

function validate(request: Request, params: Readonly<Record<string, string | readonly string[] | undefined>>): Response | null {
  const projectId = typeof params.projectId === 'string' ? params.projectId : '';
  const query = new URL(request.url).searchParams;
  if (!request.headers.get('authorization')?.startsWith('Bearer ') || request.headers.get('x-organization-id') !== 'org_fx_01' || request.headers.get('x-project-id') !== projectId || request.headers.get('x-region-code') !== query.get('region_code') || !request.headers.get('x-client-version')) return failure(400, 'INVALID_HEADERS', 'req_fx_p12_headers');
  if (!query.get('region_code')) return failure(400, 'INVALID_QUERY', 'req_fx_p12_region');
  return null;
}

async function gate(endpoint: string): Promise<Response | null> {
  const scenario = getStorageOverviewScenario();
  if (scenario === 'first-loading') await delay(2_000);
  if (scenario === 'forbidden') return failure(403, 'CAPABILITY_MISSING', `req_fx_p12_${endpoint}_403`);
  if (scenario === 'not-found') return failure(404, 'STORAGE_SNAPSHOT_NOT_FOUND', `req_fx_p12_${endpoint}_404`);
  if (scenario === 'gone') return failure(410, 'SNAPSHOT_EXPIRED', `req_fx_p12_${endpoint}_410`);
  if (scenario === 'conflict' && endpoint === 'objects') return failure(409, 'SNAPSHOT_CHANGED', 'req_fx_p12_objects_409');
  if (scenario === 'fatal-error' && endpoint === 'overview') return failure(503, 'STORAGE_OVERVIEW_UNAVAILABLE', 'req_fx_p12_overview_503');
  if (scenario === 'partial-error' && endpoint === 'objects') return failure(503, 'STORAGE_OBJECTS_UNAVAILABLE', 'req_fx_p12_objects_503');
  if (scenario === 'rate-limited' && endpoint === 'objects') return HttpResponse.json({ error: { code: 'RATE_LIMITED', message: 'rate limited', field_errors: [], operation_errors: [], blocked_reasons: [], request_id: 'req_fx_p12_429', retryable: true } }, { status: 429, headers: { 'Retry-After': '3' } });
  if (storageOfflineShouldFail()) return HttpResponse.error();
  return null;
}

export const storageOverviewHandlers = [
  http.get('*/api/v1/projects/:projectId/storage/overview', async ({ request, params }) => {
    const invalid = validate(request, params); if (invalid) return invalid;
    const months = new URL(request.url).searchParams.get('months') ?? '6';
    if (!['3', '6', '12'].includes(months)) return failure(400, 'INVALID_QUERY', 'req_fx_p12_months');
    const gated = await gate('overview'); if (gated) return gated;
    if (getStorageOverviewScenario() === 'unknown-enum') return HttpResponse.json(storageUnknownFixtures.overview);
    if (getStorageOverviewScenario() === 'contract-mismatch') return HttpResponse.json({ ...storageOverviewFixture, data: { ...storageOverviewFixture.data, totals: { ...storageOverviewFixture.data.totals, actual_oss_physical_bytes: { state: 'KNOWN', value: 2147483648 } } } });
    if (getStorageOverviewScenario() === 'empty') return HttpResponse.json({ ...storageOverviewFixture, data: { ...storageOverviewFixture.data, roles: [], growth: [], storage_classes: [], alerts: [], totals: { ...storageOverviewFixture.data.totals, object_count: { state: 'KNOWN', value: '0' } } } });
    return HttpResponse.json(storageOverviewFixture);
  }),
  http.get('*/api/v1/projects/:projectId/storage/objects', async ({ request, params }) => {
    const invalid = validate(request, params); if (invalid) return invalid;
    const query = new URL(request.url).searchParams;
    if (!['20', '50', '100'].includes(query.get('limit') ?? '') || !['physical_bytes:desc,object_id:desc', 'created_at:desc,object_id:desc'].includes(query.get('sort') ?? '') || (query.has('after') && query.has('before'))) return failure(400, 'INVALID_QUERY', 'req_fx_p12_objects_query');
    const gated = await gate('objects'); if (gated) return gated;
    if (getStorageOverviewScenario() === 'empty' || getStorageOverviewScenario() === 'filtered-empty') return HttpResponse.json(storageEmptyInventoryFixture);
    if (getStorageOverviewScenario() === 'unknown-enum') return HttpResponse.json(storageUnknownFixtures.inventory);
    return HttpResponse.json(storageInventoryFixture);
  }),
  http.get('*/api/v1/projects/:projectId/storage/objects/:objectId', async ({ request, params }) => {
    const invalid = validate(request, params); if (invalid) return invalid;
    const query = new URL(request.url).searchParams;
    const objectId = typeof params.objectId === 'string' ? params.objectId : '';
    if (!query.get('snapshot_id') || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(objectId)) return failure(400, 'INVALID_QUERY', 'req_fx_p12_object_detail_query');
    const gated = await gate('object'); if (gated) return gated;
    if (objectId !== storageObjectDetailFixture.data.object_id) return failure(404, 'STORAGE_OBJECT_NOT_FOUND', 'req_fx_p12_object_404');
    return HttpResponse.json(storageObjectDetailFixture);
  }),
  http.get('*/api/v1/projects/:projectId/storage/multipart', async ({ request, params }) => {
    const invalid = validate(request, params); if (invalid) return invalid;
    const query = new URL(request.url).searchParams;
    if (!['20', '50', '100'].includes(query.get('limit') ?? '') || query.get('sort') !== 'last_activity_at:asc,multipart_id:asc' || (query.has('after') && query.has('before'))) return failure(400, 'INVALID_QUERY', 'req_fx_p12_multipart_query');
    const gated = await gate('multipart'); if (gated) return gated;
    const scenario = getStorageOverviewScenario();
    if (scenario === 'empty' || scenario === 'filtered-empty') return HttpResponse.json(storageEmptyMultipartFixture);
    if (scenario === 'unknown-enum') return HttpResponse.json(storageUnknownFixtures.multipart);
    return HttpResponse.json(storageMultipartFixture);
  }),
  http.get('*/api/v1/projects/:projectId/storage/cost-breakdown', async ({ request, params }) => {
    const invalid = validate(request, params); if (invalid) return invalid;
    const billing = new URL(request.url).searchParams.get('billing_period');
    if (billing && !/^\d{4}-(0[1-9]|1[0-2])$/.test(billing)) return failure(400, 'INVALID_QUERY', 'req_fx_p12_cost_query');
    const gated = await gate('cost'); if (gated) return gated;
    return HttpResponse.json(storageCostFixture);
  }),
];

export default storageOverviewHandlers;
