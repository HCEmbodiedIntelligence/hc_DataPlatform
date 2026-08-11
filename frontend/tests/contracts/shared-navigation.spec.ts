import { describe, expect, it } from 'vitest';
import { makeScopeKey } from '../../src/entities/scope';
import {
  filterNavigationManifest,
  navigationManifest,
  resolveGroupLanding,
} from '../../src/app/shell/navigation-manifest';

describe('navigation manifest', () => {
  it('keeps the seven fixed groups and fixed settings order', () => {
    expect(navigationManifest.map((group) => group.groupId)).toEqual([
      'dashboard', 'ingest', 'datasets', 'annotation', 'manual', 'storage', 'settings',
    ]);
    expect(navigationManifest.find((group) => group.groupId === 'settings')?.items.map((item) => item.pageId)).toEqual([
      'P14', 'P15', 'P16', 'P17', 'P18', 'P19',
    ]);
  });

  it('filters fail closed and resolves the first legal group landing', () => {
    const availability = { P02: true, P03: true };
    const snapshot = {
      scopeKey: makeScopeKey({ organizationId: 'org_fx_01' }),
      roleVersion: 'role_version_fx_01',
      capabilities: ['ingest_source.read'] as const,
      fetchedAt: '2026-08-05T08:00:00Z',
    };
    expect(resolveGroupLanding('ingest', snapshot, availability)).toBe('/ingest/sources');
    expect(filterNavigationManifest(new Set(), availability).some((group) => group.groupId === 'ingest')).toBe(false);
  });
});
