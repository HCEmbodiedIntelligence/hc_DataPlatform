import type { CanonicalAuditEventName } from './event-catalog';

export type AuditOutcomeFilter = 'SUCCEEDED' | 'DENIED' | 'FAILED' | 'PARTIAL';
export type AuditRiskFilter = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type AuditSearch = Readonly<{
  from: string;
  to: string;
  actorId: readonly string[];
  eventName: readonly CanonicalAuditEventName[];
  resourceType: readonly string[];
  resourceId?: string;
  result: readonly AuditOutcomeFilter[];
  riskLevel: readonly AuditRiskFilter[];
  requestId?: string;
  eventId?: string;
  sort: 'occurredAt:desc';
  after?: string;
  before?: string;
  limit: 20 | 50 | 100;
  returnTo?: string;
}>;

const defaultTo = new Date(Math.floor(Date.now() / 1000) * 1000).toISOString();
const defaultFrom = new Date(Date.parse(defaultTo) - 24 * 60 * 60 * 1000).toISOString();

export const AUDIT_SEARCH_DEFAULTS: AuditSearch = {
  from: defaultFrom,
  to: defaultTo,
  actorId: [],
  eventName: [],
  resourceType: [],
  result: [],
  riskLevel: [],
  sort: 'occurredAt:desc',
  limit: 50,
};

export function buildAuditSearchParams(value: Partial<AuditSearch>): URLSearchParams {
  const normalized = { ...AUDIT_SEARCH_DEFAULTS, ...value };
  const params = new URLSearchParams();
  if (normalized.from !== AUDIT_SEARCH_DEFAULTS.from) params.set('from', normalized.from);
  if (normalized.to !== AUDIT_SEARCH_DEFAULTS.to) params.set('to', normalized.to);
  for (const actorId of [...new Set(normalized.actorId)].sort()) params.append('actorId', actorId);
  for (const eventName of [...new Set(normalized.eventName)].sort()) params.append('eventName', eventName);
  for (const resourceType of [...new Set(normalized.resourceType)].sort()) params.append('resourceType', resourceType);
  if (normalized.resourceId) params.set('resourceId', normalized.resourceId);
  for (const result of [...new Set(normalized.result)].sort()) params.append('result', result);
  for (const riskLevel of [...new Set(normalized.riskLevel)].sort()) params.append('riskLevel', riskLevel);
  if (normalized.requestId) params.set('requestId', normalized.requestId);
  if (normalized.eventId) params.set('eventId', normalized.eventId);
  if (normalized.after) params.set('after', normalized.after);
  else if (normalized.before) params.set('before', normalized.before);
  if (normalized.limit !== 50) params.set('limit', String(normalized.limit));
  if (normalized.returnTo) params.set('returnTo', normalized.returnTo);
  return params;
}

export const auditRoute = {
  path: '/settings/audit',
  build(search: Partial<AuditSearch> = {}): string {
    const query = buildAuditSearchParams(search).toString();
    return query ? `/settings/audit?${query}` : '/settings/audit';
  },
} as const;

const cursorResetKeys: readonly (keyof AuditSearch)[] = [
  'from', 'to', 'actorId', 'eventName', 'resourceType', 'resourceId',
  'result', 'riskLevel', 'requestId', 'sort', 'limit',
];

export function patchAuditSearch(current: AuditSearch, patch: Partial<AuditSearch>): AuditSearch {
  const changed = cursorResetKeys.some((key) => key in patch && JSON.stringify(patch[key]) !== JSON.stringify(current[key]));
  const next = { ...current, ...patch };
  if (!changed) return next;
  return { ...next, after: undefined, before: undefined };
}
