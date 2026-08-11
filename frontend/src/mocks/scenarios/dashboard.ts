import { makeScopeKey } from '../../entities/scope';
import { useShellStore } from '../../shared/scope/shell-store';
import { registerScenario } from './registry';

export const dashboardScenarioIds = [
  'happy', 'empty', 'filtered-empty', 'first-loading', 'refreshing', 'partial-error', 'fatal-error',
  'forbidden', 'not-found', 'gone', 'conflict', 'rate-limited', 'offline-recovery',
  'unknown-enum', 'contract-mismatch', 'scope-switch-race', 'feature-unavailable',
] as const;

export type DashboardScenarioId = (typeof dashboardScenarioIds)[number];
let current: DashboardScenarioId = 'happy';
let offlineAttempts = 0;

export function setDashboardScenario(scenario: DashboardScenarioId): void {
  current = scenario;
  offlineAttempts = 0;
}

export function getDashboardScenario(): DashboardScenarioId {
  const params = new URLSearchParams(globalThis.location?.search ?? '');
  const requested = params.get('mockScenario') ?? params.get('t2Scenario');
  const normalized = requested?.startsWith('dashboard:') ? requested.slice('dashboard:'.length) : requested;
  if (normalized && dashboardScenarioIds.includes(normalized as DashboardScenarioId)) return normalized as DashboardScenarioId;
  return current;
}

export function dashboardOfflineShouldFail(): boolean {
  offlineAttempts += 1;
  return current === 'offline-recovery' && offlineAttempts <= 3;
}

const mockScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' } as const;

for (const scenario of dashboardScenarioIds) {
  registerScenario('dashboard', scenario, () => {
    setDashboardScenario(scenario);
    const shell = useShellStore.getState();
    shell.setSession({ actorId: 'usr_fx_dashboard', displayName: 'Fixture 工作台读取者', roleIds: ['PROJECT_DEVELOPER'] }, 'fixture-bearer');
    if (scenario !== 'feature-unavailable') shell.setScope(mockScope);
    shell.setAuthorization({
      scopeKey: scenario === 'feature-unavailable' ? shell.scopeKey : makeScopeKey(mockScope),
      roleVersion: 'role_fx_dashboard_01',
      capabilities: [],
      fetchedAt: '2026-08-05T08:00:00Z',
    });
    // dashboard.read remains a reserved capability. Mock mode explicitly enables it,
    // while this failure bit keeps the forbidden fixture fail-closed with zero reads.
    if (scenario === 'forbidden') shell.setAuthorizationFailed();
    return () => setDashboardScenario('happy');
  });
}
