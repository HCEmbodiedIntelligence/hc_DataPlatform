export const INGEST_SCENARIOS = [
  'happy', 'empty', 'filtered-empty', 'first-loading', 'refreshing', 'partial-error', 'fatal-error', 'forbidden', 'not-found', 'gone', 'conflict',
  'rate-limited', 'offline-recovery', 'unknown-enum', 'contract-mismatch', 'validation-error', 'preflight-blocked', 'submitting',
  'accepted-job', 'job-failed', 'duplicate-idempotency', 'etag-conflict', 'permission-revoked', 'signed-url-expired', 'sse-disconnected',
  'scope-switch-race', 'feature-unavailable',
] as const;
export type IngestScenario = (typeof INGEST_SCENARIOS)[number];

let activeScenario: IngestScenario = 'happy';
export function setIngestScenario(scenario: IngestScenario): void { activeScenario = scenario; }
export function getIngestScenario(): IngestScenario {
  if (typeof globalThis.location !== 'undefined') {
    const requested = new URLSearchParams(globalThis.location.search).get('mockScenario');
    if (requested && (INGEST_SCENARIOS as readonly string[]).includes(requested)) return requested as IngestScenario;
  }
  return activeScenario;
}
export function resetIngestScenario(): void { activeScenario = 'happy'; }

const mockScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' } as const;
const allIngestCapabilities = ['ingest_source.read', 'ingest_source.manage', 'upload.read', 'upload.manage'] as const;

for (const scenario of INGEST_SCENARIOS) {
  registerScenario('ingest', scenario, () => {
    setIngestScenario(scenario);
    const shell = useShellStore.getState();
    shell.setSession({ actorId: 'user_fx_01', displayName: 'Fixture Operator', roleIds: ['PROJECT_DEVELOPER'] }, 'fixture-bearer');
    if (scenario !== 'feature-unavailable') shell.setScope(mockScope);
    shell.setAuthorization({
      scopeKey: scenario === 'feature-unavailable' ? useShellStore.getState().scopeKey : makeScopeKey(mockScope),
      roleVersion: 'role_fx_ingest_01',
      capabilities: scenario === 'forbidden' || scenario === 'permission-revoked' ? [] : allIngestCapabilities,
      fetchedAt: '2026-08-05T08:00:00Z',
    });
    return () => resetIngestScenario();
  });
}
import { makeScopeKey } from '../../entities/scope';
import { useShellStore } from '../../shared/scope/shell-store';
import { registerScenario } from './registry';
