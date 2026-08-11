import { delay, http, HttpResponse } from 'msw';
import { canonicalAuditEventNames } from '../../features/audit/event-catalog';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  auditCapabilityProjectionFixtures,
  auditBootstrapFixture,
  auditEmptyEventsFixture,
  auditEventsFixture,
  auditFacetsFixture,
  auditUnknownEventsFixture,
} from '../fixtures/audit';
import { auditOfflineShouldFail, getAuditScenario } from '../scenarios/audit';

const canonicalEvents = new Set<string>(canonicalAuditEventNames);
const stableId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const resourceType = /^[A-Z][A-Z0-9_]{0,127}$/;
const outcomes = new Set(['SUCCEEDED', 'DENIED', 'FAILED', 'PARTIAL']);
const risks = new Set(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']);

function failure(status: number, code: string, requestId: string): Response {
  return HttpResponse.json({
    error: {
      code,
      message: code,
      field_errors: [],
      operation_errors: [],
      blocked_reasons: [],
      request_id: requestId,
      retryable: status === 429 || status >= 500,
    },
  }, { status });
}

function validateHeaders(
  request: Request,
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
): Response | null {
  const projectId = typeof params.projectId === 'string' ? params.projectId : '';
  const headers = request.headers;
  if (
    !headers.get('authorization')?.startsWith('Bearer ') ||
    headers.get('x-organization-id') !== 'org_fx_01' ||
    headers.get('x-project-id') !== projectId ||
    headers.get('x-region-code') !== 'cn-shanghai' ||
    !headers.get('x-client-version')
  ) {
    return failure(400, 'INVALID_HEADERS', 'req_fx_p19_headers');
  }
  return null;
}

function validateTimeRange(query: URLSearchParams): Response | null {
  const from = query.get('occurred_from');
  const to = query.get('occurred_to');
  if (
    from === null ||
    to === null ||
    !Number.isFinite(Date.parse(from)) ||
    !Number.isFinite(Date.parse(to)) ||
    Date.parse(from) >= Date.parse(to)
  ) {
    return failure(422, 'INVALID_TIME_RANGE', 'req_fx_p19_time_range');
  }
  return null;
}

function validateFilters(query: URLSearchParams, list: boolean): Response | null {
  const allowed = new Set([
    'occurred_from', 'occurred_to', 'actor_id', 'event_name', 'resource_type',
    'resource_id', 'outcome', 'risk_level', 'request_id', 'region_code', ...(list ? ['sort', 'after', 'before', 'limit'] : []),
  ]);
  if ([...query.keys()].some((key) => !allowed.has(key))) {
    return failure(400, 'UNKNOWN_QUERY', 'req_fx_p19_unknown_query');
  }
  const invalidTime = validateTimeRange(query);
  if (invalidTime) return invalidTime;
  if (query.getAll('actor_id').some((value) => !stableId.test(value))) return failure(422, 'INVALID_ACTOR', 'req_fx_p19_actor');
  if (query.getAll('event_name').some((value) => !canonicalEvents.has(value))) return failure(422, 'INVALID_EVENT_NAME', 'req_fx_p19_event');
  if (query.getAll('resource_type').some((value) => !resourceType.test(value) || value === 'RETIRED_RESOURCE')) return failure(422, 'INVALID_RESOURCE_TYPE', 'req_fx_p19_resource_type');
  if (query.getAll('resource_id').some((value) => !stableId.test(value))) return failure(422, 'INVALID_RESOURCE_ID', 'req_fx_p19_resource_id');
  if (query.getAll('outcome').some((value) => !outcomes.has(value))) return failure(422, 'INVALID_OUTCOME', 'req_fx_p19_outcome');
  if (query.getAll('risk_level').some((value) => !risks.has(value))) return failure(422, 'INVALID_RISK', 'req_fx_p19_risk');
  if (query.getAll('request_id').some((value) => !stableId.test(value))) return failure(422, 'INVALID_REQUEST_ID', 'req_fx_p19_request');
  if (query.getAll('region_code').some((value) => value !== 'cn-shanghai')) return failure(422, 'INVALID_REGION', 'req_fx_p19_region');
  if (!list) return null;
  if (query.get('sort') !== 'occurred_at:desc,event_id:desc') return failure(422, 'UNSTABLE_SORT', 'req_fx_p19_sort');
  if (!['20', '50', '100'].includes(query.get('limit') ?? '')) return failure(422, 'INVALID_LIMIT', 'req_fx_p19_limit');
  if (query.has('after') && query.has('before')) return failure(422, 'AMBIGUOUS_CURSOR', 'req_fx_p19_cursor');
  if (query.getAll('region_code').length !== 1) return failure(422, 'INVALID_REGION', 'req_fx_p19_region');
  return null;
}

async function gate(endpoint: 'bootstrap' | 'facets' | 'events' | 'detail'): Promise<Response | null> {
  const scenario = getAuditScenario();
  if (scenario === 'first-loading') await delay(2_000);
  if (scenario === 'forbidden') return failure(403, 'FORBIDDEN', `req_fx_p19_${endpoint}_403`);
  if (scenario === 'not-found') return failure(404, 'NOT_FOUND', `req_fx_p19_${endpoint}_404`);
  if (scenario === 'gone' && endpoint === 'detail') return failure(410, 'AUDIT_EVENT_RETAINED_OUT', 'req_fx_p19_detail_410');
  if (scenario === 'conflict' && endpoint === 'events') return failure(409, 'AUDIT_CURSOR_AUTH_CHANGED', 'req_fx_p19_events_409');
  if (scenario === 'fatal-error' && (endpoint === 'bootstrap' || endpoint === 'events')) return failure(503, 'AUDIT_UNAVAILABLE', `req_fx_p19_${endpoint}_503`);
  if (scenario === 'partial-error' && endpoint === 'facets') return failure(503, 'AUDIT_FACETS_UNAVAILABLE', 'req_fx_p19_facets_503');
  if (scenario === 'rate-limited' && endpoint === 'events') {
    return HttpResponse.json({ error: { code: 'RATE_LIMITED', message: 'rate limited', field_errors: [], operation_errors: [], blocked_reasons: [], request_id: 'req_fx_p19_429', retryable: true } }, { status: 429, headers: { 'Retry-After': '3' } });
  }
  if (auditOfflineShouldFail()) return HttpResponse.error();
  return null;
}

export const auditHandlers = [
  http.get('*/api/v1/projects/:projectId/audit/bootstrap', async ({ request, params }) => {
    const invalidHeaders = validateHeaders(request, params); if (invalidHeaders) return invalidHeaders;
    const query = new URL(request.url).searchParams;
    if ([...query.keys()].some((key) => !['occurred_from', 'occurred_to'].includes(key))) return failure(400, 'UNKNOWN_QUERY', 'req_fx_p19_bootstrap_query');
    const invalidRange = validateTimeRange(query); if (invalidRange) return invalidRange;
    const gated = await gate('bootstrap'); if (gated) return gated;
    return HttpResponse.json(auditBootstrapFixture);
  }),
  http.get('*/api/v1/projects/:projectId/audit/events/facets', async ({ request, params }) => {
    const invalidHeaders = validateHeaders(request, params); if (invalidHeaders) return invalidHeaders;
    const invalidFilters = validateFilters(new URL(request.url).searchParams, false); if (invalidFilters) return invalidFilters;
    const gated = await gate('facets'); if (gated) return gated;
    return HttpResponse.json(auditFacetsFixture);
  }),
  http.get('*/api/v1/projects/:projectId/audit/events', async ({ request, params }) => {
    const invalidHeaders = validateHeaders(request, params); if (invalidHeaders) return invalidHeaders;
    const query = new URL(request.url).searchParams;
    const invalidFilters = validateFilters(query, true); if (invalidFilters) return invalidFilters;
    const gated = await gate('events'); if (gated) return gated;
    const scenario = getAuditScenario();
    if (scenario === 'empty' || scenario === 'filtered-empty') return HttpResponse.json(auditEmptyEventsFixture);
    if (scenario === 'unknown-enum') return HttpResponse.json(auditUnknownEventsFixture);
    if (scenario === 'contract-mismatch') {
      const first = auditEventsFixture.items[0];
      if (!first) return failure(500, 'FIXTURE_INVARIANT_FAILED', 'req_fx_p19_fixture');
      return HttpResponse.json({
        ...auditEventsFixture,
        items: [{ ...first, change: { ...first.change, before: { authorization_token: 'must-be-rejected' } } }],
      });
    }
    return HttpResponse.json(auditEventsFixture);
  }),
  http.get('*/api/v1/projects/:projectId/audit/events/:eventId', async ({ request, params }) => {
    const invalidHeaders = validateHeaders(request, params); if (invalidHeaders) return invalidHeaders;
    const eventId = typeof params.eventId === 'string' ? params.eventId : '';
    if (!stableId.test(eventId) || [...new URL(request.url).searchParams.keys()].length > 0) return failure(422, 'INVALID_EVENT_ID', 'req_fx_p19_detail_query');
    const gated = await gate('detail'); if (gated) return gated;
    const granted = new Set(useShellStore.getState().authorization?.capabilities ?? []);
    if (granted.has('access.read') && granted.has('audit.export')) return HttpResponse.json(auditCapabilityProjectionFixtures.fullAccess);
    if (granted.has('dataset_version.read')) return HttpResponse.json(auditCapabilityProjectionFixtures.reviewContext);
    return HttpResponse.json(auditCapabilityProjectionFixtures.readOnly);
  }),
];

export default auditHandlers;
