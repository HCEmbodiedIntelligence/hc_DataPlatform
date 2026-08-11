import { createElement } from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { adaptDashboardActivity, adaptDashboardPendingPage, adaptDashboardSnapshot } from '../../src/features/dashboard/api/adapter';
import { dashboardQueryKeys } from '../../src/features/dashboard/api/query-keys';
import { dashboardActivityWireSchema, dashboardPendingPageWireSchema, dashboardSnapshotWireSchema } from '../../src/features/dashboard/api/schemas';
import { DashboardRegionState, type DashboardRegionStatus } from '../../src/features/dashboard/region-state';
import type { DashboardScope } from '../../src/features/dashboard/types';
import { dashboardActivityFixture, dashboardEmptyFixtures, dashboardUnknownFixtures } from '../../src/mocks/fixtures/dashboard';
import dashboardQueryCodec from '../../src/pages/p01-dashboard/query-codec';
import { useShellStore } from '../../src/shared/scope/shell-store';

const scope: DashboardScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai', timezone: 'Asia/Shanghai' };
const statuses: readonly Exclude<DashboardRegionStatus, 'ready'>[] = [
  'first-loading', 'refreshing', 'empty', 'filtered-empty', 'partial-error', 'fatal-error',
  'forbidden', 'not-found-gone', 'conflict', 'rate-limited', 'offline-reconnecting',
  'contract-mismatch', 'unknown-enum', 'feature-unavailable',
];

describe('P01 dashboard contract', () => {
  it('roundtrips canonical URL state and drops unknown keys', () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const parsed = dashboardQueryCodec.parse('range=custom&from=2026-08-04T08%3A00%3A00Z&to=2026-08-05T08%3A00%3A00Z&signedUrl=discard');
    expect(dashboardQueryCodec.parse(dashboardQueryCodec.build(parsed))).toEqual(parsed);
    expect(dashboardQueryCodec.build(parsed).toString()).not.toContain('signedUrl');
    expect(warning).toHaveBeenCalledWith('query_codec_diagnostic', expect.objectContaining({ code: 'UNKNOWN_QUERY_KEY', key: 'signedUrl' }));
  });

  it('adapts aggregates without collapsing missing, null and zero', () => {
    const activity = adaptDashboardActivity(dashboardActivityWireSchema.parse(dashboardActivityFixture));
    expect(activity.acceptedUniqueBytes).toBe(1_099_511_627_776n);
    const empty = adaptDashboardActivity(dashboardActivityWireSchema.parse(dashboardEmptyFixtures.activity));
    expect(empty.acceptedUniqueBytes).toBe(0n);
    expect(empty.successRatio).toBeNull();
    const missing = structuredClone(dashboardActivityFixture) as Record<string, unknown>;
    delete ((missing.data as { uploads: Record<string, unknown> }).uploads).accepted_unique_bytes;
    expect(dashboardActivityWireSchema.safeParse(missing).success).toBe(false);
  });

  it('maps unknown enums to safe read-only states', () => {
    const snapshot = adaptDashboardSnapshot(dashboardSnapshotWireSchema.parse(dashboardUnknownFixtures.snapshot));
    const pending = adaptDashboardPendingPage(dashboardPendingPageWireSchema.parse(dashboardUnknownFixtures.pending));
    expect(snapshot.roles.at(-1)).toMatchObject({ role: 'UNKNOWN', wireRole: 'FUTURE_ROLE' });
    expect(snapshot.hasUnknownEnum).toBe(true);
    expect(pending.items.at(-1)).toMatchObject({ kind: 'UNKNOWN', clickable: false });
  });

  it('isolates query keys by shell scope', () => {
    useShellStore.getState().setScope({ organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode });
    const first = dashboardQueryKeys.snapshot(scope);
    const other = { ...scope, projectId: 'prj_fx_02' };
    useShellStore.getState().setScope({ organizationId: other.organizationId, projectId: other.projectId, regionCode: other.regionCode });
    expect(dashboardQueryKeys.snapshot(other)).not.toEqual(first);
    expect(dashboardQueryKeys.snapshot({ ...scope, organizationId: 'org_fx_02' })).not.toEqual(first);
  });

  it('renders every regional state while preserving non-blocking children', () => {
    for (const status of statuses) {
      const view = render(createElement(DashboardRegionState, { status }, createElement('span', null, 'retained')));
      expect(view.container.querySelector(`[data-region-status="${status}"]`)).not.toBeNull();
      if (status === 'refreshing' || status === 'unknown-enum') expect(view.getByText('retained')).toBeVisible();
      view.unmount();
    }
    const ready = render(createElement(DashboardRegionState, { status: 'ready' }, createElement('span', null, 'ready child')));
    expect(ready.getByText('ready child')).toBeVisible();
  });
});
