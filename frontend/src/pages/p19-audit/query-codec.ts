import { defineQueryCodec, safeReturnTo } from '../../shared/routing/route-registry';
import { isCanonicalAuditEventName } from '../../features/audit/event-catalog';
import {
  AUDIT_SEARCH_DEFAULTS,
  buildAuditSearchParams,
  type AuditOutcomeFilter,
  type AuditRiskFilter,
  type AuditSearch,
} from '../../features/audit/routing';

const outcomes = new Set<AuditOutcomeFilter>(['SUCCEEDED', 'DENIED', 'FAILED', 'PARTIAL']);
const risks = new Set<AuditRiskFilter>(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']);
const limits = new Set([20, 50, 100]);
const stableId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const resourceTypePattern = /^[A-Z][A-Z0-9_]{0,127}$/;

export const auditQueryCodec = defineQueryCodec<AuditSearch>({
  defaults: AUDIT_SEARCH_DEFAULTS,
  parse(params) {
    const fromRaw = params.get('from') ?? AUDIT_SEARCH_DEFAULTS.from;
    const toRaw = params.get('to') ?? AUDIT_SEARCH_DEFAULTS.to;
    const validRange = Number.isFinite(Date.parse(fromRaw)) && Number.isFinite(Date.parse(toRaw)) && Date.parse(fromRaw) < Date.parse(toRaw);
    const after = params.get('after');
    const before = params.get('before');
    const resourceId = params.get('resourceId');
    const requestId = params.get('requestId');
    const eventId = params.get('eventId');
    const returnTo = safeReturnTo(params.get('returnTo'));
    const limitRaw = Number.parseInt(params.get('limit') ?? '50', 10);
    return {
      from: validRange ? new Date(fromRaw).toISOString() : AUDIT_SEARCH_DEFAULTS.from,
      to: validRange ? new Date(toRaw).toISOString() : AUDIT_SEARCH_DEFAULTS.to,
      actorId: [...new Set(params.getAll('actorId').filter((item) => stableId.test(item)))].sort(),
      eventName: [...new Set(params.getAll('eventName').filter(isCanonicalAuditEventName))].sort(),
      resourceType: [...new Set(params.getAll('resourceType').filter((item) => resourceTypePattern.test(item) && item !== 'RETIRED_RESOURCE'))].sort(),
      result: [...new Set(params.getAll('result').filter((item): item is AuditOutcomeFilter => outcomes.has(item as AuditOutcomeFilter)))].sort(),
      riskLevel: [...new Set(params.getAll('riskLevel').filter((item): item is AuditRiskFilter => risks.has(item as AuditRiskFilter)))].sort(),
      sort: 'occurredAt:desc',
      limit: (limits.has(limitRaw) ? limitRaw : 50) as 20 | 50 | 100,
      ...(resourceId && stableId.test(resourceId) ? { resourceId } : {}),
      ...(requestId && stableId.test(requestId) ? { requestId } : {}),
      ...(eventId && stableId.test(eventId) ? { eventId } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
      ...(returnTo ? { returnTo } : {}),
    };
  },
  build: buildAuditSearchParams,
  cursorResetKeys: ['from', 'to', 'actorId', 'eventName', 'resourceType', 'resourceId', 'result', 'riskLevel', 'requestId', 'sort', 'limit'],
});

export type { AuditSearch };
export default auditQueryCodec;
