import { makeScopeKey } from '../../entities/scope';
import type { Capability } from '../../entities/capability';
import { useShellStore } from '../../shared/scope/shell-store';
import { registerScenario } from './registry';

export const auditScenarioIds = [
  'happy', 'empty', 'filtered-empty', 'first-loading', 'refreshing', 'partial-error', 'fatal-error',
  'forbidden', 'not-found', 'gone', 'conflict', 'rate-limited', 'offline-recovery',
  'unknown-enum', 'contract-mismatch', 'scope-switch-race', 'feature-unavailable',
  'admin-view', 'developer-view', 'processor-view',
] as const;

export type AuditScenarioId = (typeof auditScenarioIds)[number];
let current: AuditScenarioId = 'happy';
let offlineAttempts = 0;

export function setAuditScenario(scenario: AuditScenarioId): void {
  current = scenario;
  offlineAttempts = 0;
}

export function getAuditScenario(): AuditScenarioId {
  const params = new URLSearchParams(globalThis.location?.search ?? '');
  const requested = params.get('mockScenario') ?? params.get('t2Scenario');
  const normalized = requested?.startsWith('audit:') ? requested.slice('audit:'.length) : requested;
  if (normalized && auditScenarioIds.includes(normalized as AuditScenarioId)) return normalized as AuditScenarioId;
  return current;
}

export function auditOfflineShouldFail(): boolean {
  offlineAttempts += 1;
  return current === 'offline-recovery' && offlineAttempts === 1;
}

const mockScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' } as const;

const capabilityProfiles: Readonly<Record<'admin-view' | 'developer-view' | 'processor-view', readonly Capability[]>> = {
  'admin-view': ['audit.read', 'audit.export', 'access.read'],
  'developer-view': ['audit.read', 'dataset_version.read'],
  'processor-view': ['audit.read'],
};

for (const scenario of auditScenarioIds) {
  registerScenario('audit', scenario, () => {
    setAuditScenario(scenario);
    const shell = useShellStore.getState();
    const profile = scenario === 'admin-view' || scenario === 'developer-view' || scenario === 'processor-view'
      ? scenario
      : 'admin-view';
    const actor = profile === 'admin-view'
      ? { actorId: 'usr_fx_admin', displayName: 'Fixture 管理员', roleIds: ['PROJECT_ADMIN'] as const }
      : profile === 'developer-view'
        ? { actorId: 'usr_fx_developer', displayName: 'Fixture 开发者', roleIds: ['PROJECT_DEVELOPER'] as const }
        : { actorId: 'usr_fx_processor', displayName: 'Fixture 数据处理员', roleIds: ['PROJECT_DATA_PROCESSOR'] as const };
    shell.setSession(actor, 'fixture-bearer');
    if (scenario !== 'feature-unavailable') shell.setScope(mockScope);
    shell.setAuthorization({
      scopeKey: scenario === 'feature-unavailable' ? shell.scopeKey : makeScopeKey(mockScope),
      roleVersion: 'role_fx_audit_projection_01',
      capabilities: scenario === 'forbidden' ? [] : capabilityProfiles[profile],
      fetchedAt: '2026-08-05T08:00:00Z',
    });
    return () => setAuditScenario('happy');
  });
}
