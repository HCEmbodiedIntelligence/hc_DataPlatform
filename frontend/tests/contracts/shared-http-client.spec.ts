import { afterEach, describe, expect, it, vi } from 'vitest';
import { configureRuntime } from '../../src/shared/config/runtime';
import { isDomainError } from '../../src/shared/api/domain-error';
import { request } from '../../src/shared/api/http-client';
import { useShellStore } from '../../src/shared/scope/shell-store';

describe('shared HTTP client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useShellStore.getState().finishScopeChange();
  });

  it('injects scope/auth headers and converts camelCase query keys', async () => {
    configureRuntime({ apiBaseUrl: 'https://api.fixture.invalid/api/v1', sseBaseUrl: 'https://sse.fixture.invalid', buildVersion: 'web-fixture', releaseEnv: 'test' });
    useShellStore.getState().setSession({ actorId: 'usr_fx_admin', displayName: 'Admin', roleIds: ['PROJECT_ADMIN'] }, 'fixture-bearer');
    useShellStore.getState().setScope({ organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await request<{ ok: boolean }>({
      method: 'POST',
      path: '/resources',
      query: { createdAfter: '2026-08-05T08:00:00Z', allowedState: ['READY', 'REVIEWING'] },
      body: { resource_id: 'dataset_fx_01' },
      idempotencyKey: 'idem_fx_01',
      ifMatch: '"rv-7"',
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('created_after=');
    expect(url).toContain('allowed_state=READY&allowed_state=REVIEWING');
    const headers = new Headers(init.headers);
    expect(headers.get('Authorization')).toBe('Bearer fixture-bearer');
    expect(headers.get('X-Organization-Id')).toBe('org_fx_01');
    expect(headers.get('X-Project-Id')).toBe('prj_fx_01');
    expect(headers.get('X-Region-Code')).toBe('cn-shanghai');
    expect(headers.get('X-Client-Version')).toBe('web-fixture');
    expect(headers.get('Idempotency-Key')).toBe('idem_fx_01');
    expect(headers.get('If-Match')).toBe('"rv-7"');
    expect(headers.get('Accept-Language')).toBeTruthy();
  });

  it('forwards an explicit no-store cache mode for short-lived authorization reads', async () => {
    configureRuntime({ apiBaseUrl: 'https://api.fixture.invalid', sseBaseUrl: 'https://sse.fixture.invalid', buildVersion: 'web-fixture', releaseEnv: 'test' });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await request({ method: 'GET', path: '/uploads/up_fx_01/authorization', cache: 'no-store' });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.cache).toBe('no-store');
  });

  it('normalizes transport failures to NETWORK_ERROR', async () => {
    configureRuntime({ apiBaseUrl: 'https://api.fixture.invalid', sseBaseUrl: 'https://sse.fixture.invalid', buildVersion: 'web-fixture', releaseEnv: 'test' });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('socket details must stay internal')));
    let thrown: unknown;
    try {
      await request({ method: 'GET', path: '/network-failure' });
    } catch (error) {
      thrown = error;
    }
    expect(isDomainError(thrown)).toBe(true);
    if (isDomainError(thrown)) {
      expect(thrown.code).toBe('NETWORK_ERROR');
      expect(thrown.message).not.toContain('socket details');
    }
  });

  it('blocks writes while the shell is switching scope', async () => {
    configureRuntime({ apiBaseUrl: 'https://api.fixture.invalid', sseBaseUrl: 'https://sse.fixture.invalid', buildVersion: 'web-fixture', releaseEnv: 'test' });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    useShellStore.getState().beginScopeChange();
    await expect(request({ method: 'POST', path: '/commands', body: {} })).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
      blockedReasons: [{ code: 'SCOPE_SWITCH_IN_PROGRESS' }],
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('drops an old-scope response that arrives after a scope change', async () => {
    configureRuntime({ apiBaseUrl: 'https://api.fixture.invalid', sseBaseUrl: 'https://sse.fixture.invalid', buildVersion: 'web-fixture', releaseEnv: 'test' });
    useShellStore.getState().setScope({ organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' });
    let resolveResponse: ((response: Response) => void) | undefined;
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pendingResponse));
    const pendingRequest = request({ method: 'GET', path: '/old-scope' });
    useShellStore.getState().setScope({ organizationId: 'org_fx_02', projectId: 'prj_fx_02', regionCode: 'cn-shanghai' });
    resolveResponse?.(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await expect(pendingRequest).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
      blockedReasons: [{ code: 'SCOPE_CHANGED' }],
    });
  });
});
