import { createElement } from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { adaptStorageInventoryPage, adaptStorageMultipartPage, adaptStorageObjectEnvelope, adaptStorageOverview } from '../../src/features/storage-overview/api/adapter';
import { storageOverviewQueryKeys } from '../../src/features/storage-overview/api/query-keys';
import { storageMultipartPageWireSchema, storageObjectEnvelopeWireSchema, storageObjectPageWireSchema, storageOverviewWireSchema } from '../../src/features/storage-overview/api/schemas';
import { displayByteMetric, displayMinorUnitMetric, formatByteString } from '../../src/features/storage-overview/metrics-contract';
import { StorageRegionState, type StorageRegionStatus } from '../../src/features/storage-overview/region-state';
import { patchStorageOverviewSearch, STORAGE_OVERVIEW_DEFAULTS } from '../../src/features/storage-overview/routing';
import type { StorageScope } from '../../src/features/storage-overview/types';
import { storageInventoryFixture, storageMultipartFixture, storageObjectDetailFixture, storageOverviewFixture, storageUnknownFixtures } from '../../src/mocks/fixtures/storage-overview';
import storageOverviewQueryCodec from '../../src/pages/p12-storage-overview/query-codec';
import { useShellStore } from '../../src/shared/scope/shell-store';

const scope: StorageScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' };
const statuses: readonly Exclude<StorageRegionStatus, 'ready'>[] = [
  'first-loading', 'refreshing', 'empty', 'filtered-empty', 'partial-error', 'fatal-error',
  'forbidden', 'not-found-gone', 'conflict', 'rate-limited', 'offline-reconnecting',
  'contract-mismatch', 'unknown-enum', 'feature-unavailable',
];

describe('P12 storage overview contract', () => {
  it('roundtrips URL state, normalizes unknown keys and clears cursors on dimensions', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const parsed = storageOverviewQueryCodec.parse('tab=objects&objectRole=SOURCE&storageClass=STANDARD&anomaly=LARGE_OBJECT&status=AVAILABLE&months=12&limit=100&after=cursor_a&unknown=discard');
    expect(storageOverviewQueryCodec.parse(storageOverviewQueryCodec.build(parsed))).toEqual(parsed);
    expect(storageOverviewQueryCodec.build(parsed).toString()).not.toContain('unknown');
    const codecChanged = storageOverviewQueryCodec.parse(storageOverviewQueryCodec.build({ ...parsed, limit: 50 }, parsed));
    expect(codecChanged.after).toBeUndefined();
    expect(codecChanged.before).toBeUndefined();
    const changed = patchStorageOverviewSearch(parsed, { limit: 50 });
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();
  });

  it('keeps byte and minor-unit values as exact decimal strings', () => {
    const overview = adaptStorageOverview(storageOverviewWireSchema.parse(storageOverviewFixture));
    expect(overview.totals.actualOssPhysicalBytes).toEqual({ state: 'KNOWN', value: '2147483648' });
    expect(formatByteString('9223372036854775807' as never)).toContain('PiB');
    expect(displayByteMetric({ state: 'KNOWN', value: '0' as never })).toBe('0 B');
    expect(displayMinorUnitMetric({ state: 'KNOWN', value: '128800' as never }, 'CNY')).toBe('CNY 1288.00');
  });

  it('adapts Inventory facts and makes unknown enums read-only', () => {
    const page = adaptStorageInventoryPage(storageObjectPageWireSchema.parse(storageInventoryFixture));
    expect(page.items[0]).toMatchObject({ masked: true, readOnly: true, physicalBytes: '1073741824' });
    const unknown = adaptStorageInventoryPage(storageObjectPageWireSchema.parse(storageUnknownFixtures.inventory));
    expect(unknown.items[0]).toMatchObject({ objectRole: 'UNKNOWN', storageClass: 'UNKNOWN', readOnly: true });
    expect(unknown.hasUnknownEnum).toBe(true);
  });

  it('adapts object detail and Multipart bytes through shared int64 strings', () => {
    const detail = adaptStorageObjectEnvelope(storageObjectEnvelopeWireSchema.parse(storageObjectDetailFixture));
    const multipart = adaptStorageMultipartPage(storageMultipartPageWireSchema.parse(storageMultipartFixture));
    const unknown = adaptStorageMultipartPage(storageMultipartPageWireSchema.parse(storageUnknownFixtures.multipart));
    expect(detail).toMatchObject({ objectId: 'storage_object_fx_02', physicalBytes: '1073741824', readOnly: true });
    expect(multipart.items[0]).toMatchObject({ receivedBytes: '1073741824', partCount: '8', readOnly: true });
    expect(unknown.items[0]).toMatchObject({ status: 'UNKNOWN', statusKnown: false, readOnly: true });
  });

  it('isolates query keys by scope and keeps the default server limit at 50', () => {
    useShellStore.getState().setScope({ organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode });
    const first = storageOverviewQueryKeys.inventory(scope, STORAGE_OVERVIEW_DEFAULTS);
    const other = { ...scope, regionCode: 'cn-beijing' };
    useShellStore.getState().setScope({ organizationId: other.organizationId, projectId: other.projectId, regionCode: other.regionCode });
    expect(storageOverviewQueryKeys.inventory(other, STORAGE_OVERVIEW_DEFAULTS)).not.toEqual(first);
    expect(storageOverviewQueryKeys.inventory({ ...scope, organizationId: 'org_fx_02' }, STORAGE_OVERVIEW_DEFAULTS)).not.toEqual(first);
    expect(STORAGE_OVERVIEW_DEFAULTS.limit).toBe(50);
  });

  it('renders every regional state and only preserves children for non-blocking states', () => {
    for (const status of statuses) {
      const view = render(createElement(StorageRegionState, { status }, createElement('span', null, 'retained')));
      expect(view.container.querySelector(`[data-region-status="${status}"]`)).not.toBeNull();
      if (status === 'refreshing' || status === 'unknown-enum') expect(view.getByText('retained')).toBeVisible();
      view.unmount();
    }
    const ready = render(createElement(StorageRegionState, { status: 'ready' }, createElement('span', null, 'ready child')));
    expect(ready.getByText('ready child')).toBeVisible();
  });
});
