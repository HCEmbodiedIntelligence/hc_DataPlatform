import { describe, expect, it } from 'vitest';
import { makeQueryKey, normalizeFilters } from '../../src/shared/api/query-keys';
import { makeScopeKey } from '../../src/entities/scope';
import { useShellStore } from '../../src/shared/scope/shell-store';

describe('shared query keys', () => {
  it('always returns a five-tuple and isolates scopes', () => {
    const firstScope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' };
    useShellStore.getState().setScope(firstScope);
    const first = makeQueryKey('datasets', 'list', { status: 'READY' });
    expect(first).toHaveLength(5);
    expect(first).toEqual(['datasets', makeScopeKey(firstScope), 'list', { status: 'READY' }, undefined]);

    const secondScope = { ...firstScope, projectId: 'prj_fx_02' };
    useShellStore.getState().setScope(secondScope);
    const second = makeQueryKey('datasets', 'list', { status: 'READY' });
    expect(second[1]).toBe(makeScopeKey(secondScope));
    expect(second).not.toEqual(first);
  });

  it('normalizes filters deterministically and idempotently', () => {
    const defaults = { limit: 50, status: 'ALL', q: '' };
    const normalized = normalizeFilters({ status: 'READY', q: '', limit: 50, owner: 'usr_fx_admin' }, defaults);
    expect(Object.keys(normalized)).toEqual(['owner', 'status']);
    expect(normalized).toEqual({ owner: 'usr_fx_admin', status: 'READY' });
    expect(normalizeFilters(normalized, defaults)).toEqual(normalized);
  });

  it.each([
    ['Date', { value: new Date('2026-08-05T08:00:00Z') }],
    ['function', { value: () => 'unsafe' }],
    ['signed URL', { value: 'https://fixture.invalid/object?X-Amz-Signature=nope' }],
  ])('rejects %s values at runtime', (_name, value) => {
    expect(() => makeQueryKey('test', 'unsafe', value)).toThrow();
  });
});
