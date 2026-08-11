import { createElement } from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { adaptAuditEvent, adaptAuditEventPage } from '../../src/features/audit/api/adapter';
import { auditQueryKeys } from '../../src/features/audit/api/query-keys';
import { auditEventPageWireSchema, auditEventWireSchema } from '../../src/features/audit/api/schemas';
import { canonicalAuditEventNames, isCanonicalAuditEventName } from '../../src/features/audit/event-catalog';
import { resolveAuditFieldVisibility } from '../../src/features/audit/field-visibility';
import { AuditRegionState, type AuditRegionStatus } from '../../src/features/audit/region-state';
import { AUDIT_SEARCH_DEFAULTS, patchAuditSearch } from '../../src/features/audit/routing';
import type { AuditScope } from '../../src/features/audit/types';
import { auditEventsFixture, auditUnknownEventFixture } from '../../src/mocks/fixtures/audit';
import auditQueryCodec from '../../src/pages/p19-audit/query-codec';
import { useShellStore } from '../../src/shared/scope/shell-store';

const scope: AuditScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' };
const statuses: readonly Exclude<AuditRegionStatus, 'ready'>[] = [
  'first-loading', 'refreshing', 'empty', 'filtered-empty', 'partial-error', 'fatal-error',
  'forbidden', 'not-found-gone', 'conflict', 'rate-limited', 'offline-reconnecting',
  'contract-mismatch', 'unknown-enum', 'feature-unavailable',
];

describe('P19 audit contract', () => {
  it('roundtrips left-closed/right-open filters and clears cursors on filter changes', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const parsed = auditQueryCodec.parse('from=2026-08-04T08%3A00%3A00Z&to=2026-08-05T08%3A00%3A00Z&eventName=access.membership.role_changed&actorId=usr_fx_admin&resourceType=PROJECT_MEMBERSHIP&result=SUCCEEDED&riskLevel=HIGH&requestId=req_fx_access_02&limit=100&after=cursor_a&unknown=discard');
    expect(Date.parse(parsed.from)).toBeLessThan(Date.parse(parsed.to));
    expect(auditQueryCodec.parse(auditQueryCodec.build(parsed))).toEqual(parsed);
    const codecChanged = auditQueryCodec.parse(auditQueryCodec.build({ ...parsed, limit: 20 }, parsed));
    expect(codecChanged.after).toBeUndefined();
    expect(codecChanged.before).toBeUndefined();
    const changed = patchAuditSearch(parsed, { result: ['FAILED'] });
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();
  });

  it('validates stable event ordering and the generated 142-name registry', () => {
    const page = adaptAuditEventPage(auditEventPageWireSchema.parse(auditEventsFixture));
    expect(page.items.map((event) => event.eventId)).toEqual(['audit_event_fx_02', 'audit_event_fx_01']);
    expect(canonicalAuditEventNames).toHaveLength(142);
    expect(canonicalAuditEventNames.every(isCanonicalAuditEventName)).toBe(true);
  });

  it('shows unregistered event and role enums safely and disables related actions', () => {
    const event = adaptAuditEvent(auditEventWireSchema.parse(auditUnknownEventFixture));
    expect(event).toMatchObject({ eventNameKnown: false, readOnly: true, hasUnknownEnum: true });
    expect(event.actor.roles).toContain('HISTORICAL_ROLE');
    expect(event.allowedActions).toEqual([]);
  });

  it('isolates list and detail query keys by scope', () => {
    useShellStore.getState().setScope({ organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode! });
    const first = auditQueryKeys.events(scope, AUDIT_SEARCH_DEFAULTS);
    const other = { ...scope, projectId: 'prj_fx_02' };
    useShellStore.getState().setScope({ organizationId: other.organizationId, projectId: other.projectId, regionCode: other.regionCode! });
    expect(auditQueryKeys.events(other, AUDIT_SEARCH_DEFAULTS)).not.toEqual(first);
    expect(auditQueryKeys.event(other, 'audit_event_fx_02')).not.toEqual(auditQueryKeys.event(scope, 'audit_event_fx_02'));
    expect(auditQueryKeys.events({ ...scope, regionCode: 'cn-beijing' }, AUDIT_SEARCH_DEFAULTS)).not.toEqual(first);
    expect(auditQueryKeys.events({ ...scope, organizationId: 'org_fx_02' }, AUDIT_SEARCH_DEFAULTS)).not.toEqual(first);
  });

  it('derives the three field projections from capabilities, never role names', () => {
    const profile = (granted: readonly string[]) => resolveAuditFieldVisibility((capability) => granted.includes(capability));
    expect(profile(['audit.read', 'audit.export', 'access.read'])).toEqual({
      actorRoleIds: true, requestMetadata: true, changeValues: true, integrityEvidence: true, exportControls: true,
    });
    expect(profile(['audit.read', 'dataset_version.read'])).toEqual({
      actorRoleIds: false, requestMetadata: false, changeValues: true, integrityEvidence: false, exportControls: false,
    });
    expect(profile(['audit.read'])).toEqual({
      actorRoleIds: false, requestMetadata: false, changeValues: false, integrityEvidence: false, exportControls: false,
    });
  });

  it('renders every regional state and preserves only non-blocking children', () => {
    for (const status of statuses) {
      const view = render(createElement(AuditRegionState, { status }, createElement('span', null, 'retained')));
      expect(view.container.querySelector(`[data-region-status="${status}"]`)).not.toBeNull();
      if (status === 'refreshing' || status === 'unknown-enum') expect(view.getByText('retained')).toBeVisible();
      view.unmount();
    }
    const ready = render(createElement(AuditRegionState, { status: 'ready' }, createElement('span', null, 'ready child')));
    expect(ready.getByText('ready child')).toBeVisible();
  });
});
