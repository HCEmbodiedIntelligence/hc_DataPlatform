import { isStorageClass, isStorageObjectRole } from '../../entities/storage-inventory';
import { safeReturnTo, defineQueryCodec } from '../../shared/routing/route-registry';
import {
  buildStorageSearchParams,
  STORAGE_OVERVIEW_DEFAULTS,
  type StorageOverviewSearch,
  type StorageOverviewTab,
} from '../../features/storage-overview/routing';

const tabs = new Set<StorageOverviewTab>(['overview', 'objects', 'multipart', 'cost']);
const months = new Set([3, 6, 12]);
const limits = new Set([20, 50, 100]);
const sorts = new Set<StorageOverviewSearch['sort']>(['physicalBytes:desc,objectId:desc', 'createdAt:desc,objectId:desc']);
const safeFilter = /^[A-Z][A-Z0-9_]{0,63}$/;
const stableId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

export const storageOverviewQueryCodec = defineQueryCodec<StorageOverviewSearch>({
  defaults: STORAGE_OVERVIEW_DEFAULTS,
  parse(params) {
    const tabRaw = params.get('tab');
    const tab = tabRaw && tabs.has(tabRaw as StorageOverviewTab) ? tabRaw as StorageOverviewTab : 'overview';
    const roleRaw = params.get('objectRole');
    const classRaw = params.get('storageClass');
    const monthsRaw = Number.parseInt(params.get('months') ?? '6', 10);
    const limitRaw = Number.parseInt(params.get('limit') ?? '50', 10);
    const sortRaw = params.get('sort');
    const anomaly = [...new Set(params.getAll('anomaly').filter((item) => safeFilter.test(item)))].sort();
    const status = [...new Set(params.getAll('status').filter((item) => safeFilter.test(item)))].sort();
    const after = params.get('after');
    const before = params.get('before');
    const returnTo = safeReturnTo(params.get('returnTo'));
    const objectId = params.get('objectId');
    return {
      tab,
      anomaly,
      status,
      months: (months.has(monthsRaw) ? monthsRaw : 6) as 3 | 6 | 12,
      sort: sortRaw && sorts.has(sortRaw as StorageOverviewSearch['sort']) ? sortRaw as StorageOverviewSearch['sort'] : STORAGE_OVERVIEW_DEFAULTS.sort,
      limit: (limits.has(limitRaw) ? limitRaw : 50) as 20 | 50 | 100,
      ...(roleRaw && isStorageObjectRole(roleRaw) ? { objectRole: roleRaw } : {}),
      ...(classRaw && isStorageClass(classRaw) ? { storageClass: classRaw } : {}),
      ...(objectId && stableId.test(objectId) ? { objectId } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
      ...(returnTo ? { returnTo } : {}),
    };
  },
  build: buildStorageSearchParams,
  cursorResetKeys: ['tab', 'objectRole', 'storageClass', 'anomaly', 'status', 'months', 'sort', 'limit'],
  normalize(value) {
    if (value.tab === 'objects') return value;
    const withoutObject = { ...value };
    delete withoutObject.objectId;
    return withoutObject;
  },
});

export type { StorageOverviewSearch };
export default storageOverviewQueryCodec;
