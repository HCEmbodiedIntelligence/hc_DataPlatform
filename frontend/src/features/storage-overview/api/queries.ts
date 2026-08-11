import { useQuery } from '@tanstack/react-query';
import type { StorageOverviewSearch } from '../routing';
import type { StorageScope } from '../types';
import { getStorageCost, getStorageObject, getStorageOverview, listStorageInventory, listStorageMultipart } from './client';
import { storageOverviewQueryKeys } from './query-keys';

export function useStorageOverview(scope: StorageScope | null, months: 3 | 6 | 12, enabled: boolean) {
  return useQuery({
    queryKey: scope ? storageOverviewQueryKeys.overview(scope, months) : ['storage', 'disabled', 'overview'],
    queryFn: ({ signal }) => getStorageOverview(scope!, months, signal),
    enabled: enabled && scope !== null,
    staleTime: 300_000,
    retry: 2,
  });
}

export function useStorageInventory(scope: StorageScope | null, search: StorageOverviewSearch, enabled: boolean) {
  return useQuery({
    queryKey: scope ? storageOverviewQueryKeys.inventory(scope, search) : ['storage', 'disabled', 'objects'],
    queryFn: ({ signal }) => listStorageInventory(scope!, search, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    retry: 2,
  });
}

export function useStorageCost(scope: StorageScope | null, billingPeriod: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: scope ? storageOverviewQueryKeys.cost(scope, billingPeriod) : ['storage', 'disabled', 'cost'],
    queryFn: ({ signal }) => getStorageCost(scope!, billingPeriod, signal),
    enabled: enabled && scope !== null,
    staleTime: 900_000,
    retry: 2,
  });
}

export function useStorageObject(scope: StorageScope | null, objectId: string | undefined, snapshotId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: scope && objectId && snapshotId ? storageOverviewQueryKeys.object(scope, objectId, snapshotId) : ['storage', 'disabled', 'object'],
    queryFn: ({ signal }) => getStorageObject(scope!, objectId!, snapshotId!, signal),
    enabled: enabled && scope !== null && objectId !== undefined && snapshotId !== undefined,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useStorageMultipart(scope: StorageScope | null, search: StorageOverviewSearch, enabled: boolean) {
  return useQuery({
    queryKey: scope ? storageOverviewQueryKeys.multipart(scope, search) : ['storage', 'disabled', 'multipart'],
    queryFn: ({ signal }) => listStorageMultipart(scope!, search, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    retry: 2,
  });
}
