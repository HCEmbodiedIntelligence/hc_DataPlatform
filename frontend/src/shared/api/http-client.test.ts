import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Scope } from '../../entities/scope';
import { configureRuntime, resetRuntimeConfigForTests } from '../config/runtime';
import { useShellStore } from '../scope/shell-store';
import { request } from './http-client';

const activeScope: Scope = {
  organizationId: 'org-a',
  projectId: 'project-a',
  regionCode: 'region-a',
};

const otherScope: Scope = {
  organizationId: 'org-b',
  projectId: 'project-b',
  regionCode: 'region-b',
};

describe('scoped HTTP requests', () => {
  beforeEach(() => {
    configureRuntime({
      apiBaseUrl: '/api/v1',
      sseBaseUrl: '/api/v1',
      buildVersion: 'test-build',
      releaseEnv: 'test',
    });
    const shell = useShellStore.getState();
    shell.finishScopeChange();
    shell.setSession({ actorId: 'actor-a', displayName: 'Actor A', roleIds: [] }, 'test-token');
    shell.setScope(activeScope);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    resetRuntimeConfigForTests();
  });

  it('uses the bound scope for all scope headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await request({
      method: 'GET',
      path: '/projects/project-a/regions/region-a/upload-sessions',
      scope: activeScope,
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get('X-Organization-Id')).toBe(activeScope.organizationId);
    expect(headers.get('X-Project-Id')).toBe(activeScope.projectId);
    expect(headers.get('X-Region-Code')).toBe(activeScope.regionCode);
  });

  it('rejects a stale bound scope before issuing a request', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      request({ method: 'GET', path: '/projects/project-b/regions/region-b/upload-sessions', scope: otherScope }),
    ).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
      blockedReasons: [{ code: 'SCOPE_CHANGED' }],
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
