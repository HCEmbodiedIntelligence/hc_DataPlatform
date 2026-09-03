import { makeScopeKey } from '../../entities/scope';
import { useShellStore } from '../../shared/scope/shell-store';
import { cleaningFixtureScope } from '../fixtures/cleaning';
import { registerScenario } from './registry';

export const CLEANING_SCENARIOS = [
  'happy', 'empty', 'filtered-empty', 'first-loading', 'partial-error', 'fatal-error',
  'forbidden', 'not-found', 'gone', 'conflict', 'rate-limited', 'offline-recovery',
  'unknown-enum', 'contract-mismatch', 'scope-switch-race', 'editing-clean',
  'editing-dirty', 'returned', 'returned-successor', 'save-validation-error',
  'save-conflict', 'preview-ready', 'preview-expired', 'commit-blocked',
] as const;

export type CleaningScenario = (typeof CLEANING_SCENARIOS)[number];
let active: CleaningScenario = 'happy';

export function setCleaningScenario(scenario: CleaningScenario): void { active = scenario; }

export function getCleaningScenario(): CleaningScenario {
  if (typeof location !== 'undefined') {
    const raw = new URLSearchParams(location.search).get('mockScenario');
    const requested = raw?.includes(':') ? raw.split(':').at(-1) : raw;
    if (requested && (CLEANING_SCENARIOS as readonly string[]).includes(requested)) return requested as CleaningScenario;
  }
  return active;
}

const scope = {
  organizationId: cleaningFixtureScope.organization_id,
  projectId: cleaningFixtureScope.project_id,
  regionCode: cleaningFixtureScope.region_code,
} as const;

const capabilities = [
  'manual_issue.read', 'manual_issue.create', 'manual_issue.triage', 'manual_issue.resolve',
  'cleaning.read', 'cleaning.create', 'cleaning.edit', 'cleaning.preview', 'cleaning.submit',
  'dataset.read', 'dataset_version.read', 'episode.read',
] as const;

for (const scenario of CLEANING_SCENARIOS) {
  registerScenario('cleaning', scenario, () => {
    active = scenario;
    const shell = useShellStore.getState();
    shell.setSession({ actorId: 'principal_fx_mc_processor_01', displayName: 'Fixture Processor', roleIds: ['PROJECT_DATA_PROCESSOR'] }, 'fixture-bearer');
    shell.setScope(scope);
    shell.setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: 'role_fx_cleaning_01',
      capabilities: scenario === 'forbidden' ? [] : capabilities,
      fetchedAt: '2026-08-06T13:00:00Z',
      expiresAt: '2099-08-06T13:00:00Z',
    });
    return () => { active = 'happy'; };
  });
}
