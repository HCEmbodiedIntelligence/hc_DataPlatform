import { makeScopeKey } from '../../entities/scope';
import { CANONICAL_CAPABILITIES } from '../../entities/capability';
import { useShellStore } from '../../shared/scope/shell-store';
import { registerScenario } from './registry';

export type ManagementScenario = 'happy' | 'forbidden' | 'contract-mismatch';
let current: ManagementScenario = 'happy';

export function getManagementScenario(): ManagementScenario {
  return current;
}

for (const scenario of ['happy', 'forbidden', 'contract-mismatch'] as const) {
  registerScenario('management', scenario, () => {
    current = scenario;
    const scope = {
      organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai',
    } as const;
    const shell = useShellStore.getState();
    shell.setSession(
      { actorId: 'usr_fx_admin', displayName: 'Fixture 管理员', roleIds: ['PROJECT_ADMIN'] },
      'fixture-bearer',
    );
    shell.setScope(scope);
    shell.setAuthorization({
      scopeKey: makeScopeKey(scope), roleVersion: 'roles-v2-conditional',
      capabilities: scenario === 'forbidden' ? [] : CANONICAL_CAPABILITIES,
      fetchedAt: '2026-08-05T08:00:00Z',
    });
    return () => { current = 'happy'; };
  });
}
