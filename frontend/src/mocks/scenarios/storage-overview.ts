import { makeScopeKey } from '../../entities/scope';
import { useShellStore } from '../../shared/scope/shell-store';
import { registerScenario } from './registry';

export const storageOverviewScenarioIds = [
  'happy', 'empty', 'filtered-empty', 'first-loading', 'refreshing', 'partial-error', 'fatal-error',
  'forbidden', 'not-found', 'gone', 'conflict', 'rate-limited', 'offline-recovery',
  'unknown-enum', 'contract-mismatch', 'scope-switch-race', 'feature-unavailable',
] as const;

export type StorageOverviewScenarioId = (typeof storageOverviewScenarioIds)[number];
let current: StorageOverviewScenarioId = 'happy';
let offlineAttempts = 0;

export function setStorageOverviewScenario(scenario: StorageOverviewScenarioId): void {
  current = scenario;
  offlineAttempts = 0;
}

export function getStorageOverviewScenario(): StorageOverviewScenarioId {
  const params = new URLSearchParams(globalThis.location?.search ?? '');
  const requested = params.get('mockScenario') ?? params.get('t2Scenario');
  const normalized = requested?.startsWith('storage-overview:') ? requested.slice('storage-overview:'.length) : requested;
  if (normalized && storageOverviewScenarioIds.includes(normalized as StorageOverviewScenarioId)) return normalized as StorageOverviewScenarioId;
  return current;
}

export function storageOfflineShouldFail(): boolean {
  offlineAttempts += 1;
  return current === 'offline-recovery' && offlineAttempts === 1;
}

const mockScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' } as const;
const readCapabilities = ['storage.overview.read', 'storage.object.read', 'storage.multipart.read', 'storage.cost.read'] as const;

for (const scenario of storageOverviewScenarioIds) {
  registerScenario('storage-overview', scenario, () => {
    setStorageOverviewScenario(scenario);
    const shell = useShellStore.getState();
    shell.setSession({ actorId: 'usr_fx_storage', displayName: 'Fixture 存储读取者', roleIds: ['PROJECT_DEVELOPER'] }, 'fixture-bearer');
    if (scenario !== 'feature-unavailable') shell.setScope(mockScope);
    shell.setAuthorization({
      scopeKey: scenario === 'feature-unavailable' ? shell.scopeKey : makeScopeKey(mockScope),
      roleVersion: 'role_fx_storage_readonly_01',
      capabilities: scenario === 'forbidden' ? [] : readCapabilities,
      fetchedAt: '2026-08-05T08:00:00Z',
    });
    return () => setStorageOverviewScenario('happy');
  });
}
