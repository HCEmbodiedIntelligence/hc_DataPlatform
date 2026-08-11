import { defineQueryCodec } from '../../shared/routing/route-registry';
import type { ProjectRoleId } from '../../features/access/capability-catalog';

export interface AccessSearch {
  readonly tab: 'members' | 'roles' | 'policies';
  readonly q?: string;
  readonly roleId?: ProjectRoleId;
  readonly memberId?: string;
  readonly invitationId?: string;
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
  readonly invAfter?: string;
  readonly invBefore?: string;
  readonly invLimit: 20 | 50 | 100;
}

const defaults: AccessSearch = { tab: 'members', limit: 20, invLimit: 20 };
const tabs = ['members', 'roles', 'policies'] as const;
const roles = new Set<ProjectRoleId>(['PROJECT_ADMIN', 'PROJECT_DEVELOPER', 'PROJECT_DATA_PROCESSOR']);
const parsedLimit = (value: string | null): 20 | 50 | 100 => value === '50' ? 50 : value === '100' ? 100 : 20;

export const accessQueryCodec = defineQueryCodec<AccessSearch>({
  defaults,
  parse(sp) {
    const tab = tabs.includes(sp.get('tab') as AccessSearch['tab']) ? sp.get('tab') as AccessSearch['tab'] : defaults.tab;
    const roleId = roles.has(sp.get('roleId') as ProjectRoleId) ? sp.get('roleId') as ProjectRoleId : undefined;
    const q = sp.get('q')?.trim().slice(0, 100);
    const memberId = sp.get('memberId') || undefined;
    const invitationId = memberId ? undefined : sp.get('invitationId') || undefined;
    const after = sp.get('after') || undefined;
    const before = sp.get('before') || undefined;
    const invAfter = sp.get('invAfter') || undefined;
    const invBefore = sp.get('invBefore') || undefined;
    return { tab, limit: parsedLimit(sp.get('limit')), invLimit: parsedLimit(sp.get('invLimit')), ...(q ? { q } : {}), ...(roleId ? { roleId } : {}), ...(memberId ? { memberId } : {}), ...(invitationId ? { invitationId } : {}), ...(after && !before ? { after } : {}), ...(before && !after ? { before } : {}), ...(invAfter && !invBefore ? { invAfter } : {}), ...(invBefore && !invAfter ? { invBefore } : {}) };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    for (const key of ['q', 'roleId', 'memberId', 'invitationId', 'after', 'before', 'invAfter', 'invBefore'] as const) if (merged[key]) sp.set(key, merged[key]);
    if (merged.tab !== defaults.tab) sp.set('tab', merged.tab);
    if (merged.limit !== defaults.limit) sp.set('limit', String(merged.limit));
    if (merged.invLimit !== defaults.invLimit) sp.set('invLimit', String(merged.invLimit));
    return sp;
  },
});

