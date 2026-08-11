import { generatedCapabilityCatalog } from './capability-catalog.generated';

export type ProjectRoleId = 'PROJECT_ADMIN' | 'PROJECT_DEVELOPER' | 'PROJECT_DATA_PROCESSOR';

export interface CapabilityCatalog {
  readonly version: string;
  readonly canonical: ReadonlySet<string>;
  readonly reserved: ReadonlySet<string>;
  readonly roleCeilings: Readonly<Record<ProjectRoleId, ReadonlySet<string>>>;
}

function parseLines(raw: string, label: string): ReadonlySet<string> {
  const lines = raw.split(/\r?\n/u).map((line) => line.trim()).filter(Boolean);
  const result = new Set(lines);
  if (result.size !== lines.length) throw new Error(`${label} contains duplicate capability keys`);
  for (const key of result) {
    if (!/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/u.test(key)) throw new Error(`${label} contains an invalid capability key`);
  }
  return result;
}

export function parseCapabilityCatalog(source: typeof generatedCapabilityCatalog): CapabilityCatalog {
  const canonical = parseLines(source.canonical, 'canonical');
  const reserved = parseLines(source.reserved, 'reserved');
  for (const key of reserved) {
    if (canonical.has(key)) throw new Error('canonical and reserved capability sets overlap');
  }
  const developer = parseLines(source.roles.PROJECT_DEVELOPER, 'PROJECT_DEVELOPER');
  const processor = parseLines(source.roles.PROJECT_DATA_PROCESSOR, 'PROJECT_DATA_PROCESSOR');
  for (const [roleId, ceiling] of [['PROJECT_DEVELOPER', developer], ['PROJECT_DATA_PROCESSOR', processor]] as const) {
    for (const key of ceiling) if (!canonical.has(key)) throw new Error(`${roleId} contains a non-canonical capability`);
  }
  return {
    version: source.catalogVersion,
    canonical,
    reserved,
    roleCeilings: {
      PROJECT_ADMIN: canonical,
      PROJECT_DEVELOPER: developer,
      PROJECT_DATA_PROCESSOR: processor,
    },
  };
}

export const capabilityCatalog = parseCapabilityCatalog(generatedCapabilityCatalog);

export const projectRoles = [
  { roleId: 'PROJECT_ADMIN', displayName: '管理员' },
  { roleId: 'PROJECT_DEVELOPER', displayName: '开发者' },
  { roleId: 'PROJECT_DATA_PROCESSOR', displayName: '数据处理员' },
] as const satisfies readonly { readonly roleId: ProjectRoleId; readonly displayName: string }[];

export function getCapabilityCounts(): {
  readonly canonical: number;
  readonly reserved: number;
  readonly byRole: Readonly<Record<ProjectRoleId, number>>;
} {
  return {
    canonical: capabilityCatalog.canonical.size,
    reserved: capabilityCatalog.reserved.size,
    byRole: {
      PROJECT_ADMIN: capabilityCatalog.roleCeilings.PROJECT_ADMIN.size,
      PROJECT_DEVELOPER: capabilityCatalog.roleCeilings.PROJECT_DEVELOPER.size,
      PROJECT_DATA_PROCESSOR: capabilityCatalog.roleCeilings.PROJECT_DATA_PROCESSOR.size,
    },
  };
}

export function isCanonicalCapability(value: string): boolean {
  return capabilityCatalog.canonical.has(value);
}

