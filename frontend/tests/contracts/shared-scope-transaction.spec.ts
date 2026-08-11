import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { makeScopeKey } from '../../src/entities/scope';
import { executeScopeSwitch } from '../../src/app/shell/scope-transaction';
import { registerScopedCleanup, registerSseCleanup } from '../../src/shared/api/transport-lifecycle';
import { useShellStore } from '../../src/shared/scope/shell-store';

describe('scope switch transaction', () => {
  it('runs cancellation, release, clearing, refetch and legal navigation in order', async () => {
    const order: string[] = [];
    const queryClient = new QueryClient();
    vi.spyOn(queryClient, 'cancelQueries').mockImplementation(() => {
      order.push('cancel-http-query');
      return Promise.resolve();
    });
    vi.spyOn(queryClient, 'removeQueries').mockImplementation(() => { order.push('remove-old-cache'); });
    registerSseCleanup(() => { order.push('cancel-sse'); });
    registerScopedCleanup('media', () => { order.push('release-resource'); });

    useShellStore.getState().setScope({
      organizationId: 'org_fx_01', projectId: 'prj_fx_old', regionCode: 'cn-shanghai',
    });
    const nextScope = {
      organizationId: 'org_fx_01', projectId: 'prj_fx_new', regionCode: 'cn-shanghai',
    };
    const snapshot = {
      scopeKey: makeScopeKey(nextScope),
      roleVersion: 'role_version_fx_01',
      capabilities: ['dataset.read'] as const,
      fetchedAt: '2026-08-05T08:00:00Z',
    };

    await executeScopeSwitch(
      queryClient,
      nextScope,
      () => {
        order.push('load-authorization');
        return Promise.resolve(snapshot);
      },
      {
        clearPageState: () => { order.push('clear-page-state'); },
        reloadNavigation: () => { order.push('reload-navigation'); },
        resolveLegalPath: () => { order.push('resolve-legal-route'); return '/datasets'; },
        navigate: () => { order.push('navigate'); },
      },
    );

    expect(order).toEqual([
      'cancel-http-query',
      'cancel-sse',
      'release-resource',
      'clear-page-state',
      'remove-old-cache',
      'load-authorization',
      'reload-navigation',
      'resolve-legal-route',
      'navigate',
    ]);
    expect(useShellStore.getState()).toMatchObject({
      scopeKey: makeScopeKey(nextScope),
      scopeChanging: false,
      authorizationFailed: false,
    });
  });
});
