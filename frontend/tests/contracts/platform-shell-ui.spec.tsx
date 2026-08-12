import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ProviderHarness } from '../../src/app/providers';
import { PlatformShell, type PlatformShellProps } from '../../src/app/shell/PlatformShell';
import { navigationManifest } from '../../src/app/shell/navigation-manifest';
import type { ProjectRoleId } from '../../src/entities/actor';
import {
  CANONICAL_CAPABILITIES,
  DATA_PROCESSOR_CAPABILITIES,
  DEVELOPER_CAPABILITIES,
  type AuthorizationSnapshot,
  type Capability,
} from '../../src/entities/capability';
import { makeScopeKey, type Scope, type ScopeKey } from '../../src/entities/scope';
import { useShellStore } from '../../src/shared/scope/shell-store';

const initialScope: Scope = {
  organizationId: 'org_fx_01',
  projectId: 'prj_fx_01',
  regionCode: 'cn-shanghai',
};

const alternateScope: Scope = {
  organizationId: 'org_fx_01',
  projectId: 'prj_fx_02',
  regionCode: 'cn-shanghai',
};

const allPagesAvailable = Object.fromEntries(
  navigationManifest.flatMap((group) => group.items.map((item) => [item.pageId, true])),
);
const browserGetComputedStyle = window.getComputedStyle.bind(window);

class ResizeObserverMock implements ResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}

function mediaMatches(query: string, width: number): boolean {
  const minimum = /min-width:\s*(\d+)px/.exec(query);
  const maximum = /max-width:\s*(\d+)px/.exec(query);
  return (
    (minimum === null || width >= Number(minimum[1])) &&
    (maximum === null || width <= Number(maximum[1]))
  );
}

function installViewport(width: number): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn((query: string) => {
      const listeners = new Set<(event: MediaQueryListEvent) => void>();
      return {
        matches: mediaMatches(query, width),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(
          (_event: string, listener: (event: MediaQueryListEvent) => void) => {
            listeners.add(listener);
          },
        ),
        removeEventListener: vi.fn(
          (_event: string, listener: (event: MediaQueryListEvent) => void) => {
            listeners.delete(listener);
          },
        ),
        dispatchEvent: vi.fn(() => true),
      } as unknown as MediaQueryList;
    }),
  });
}

function seedShell(
  capabilities: readonly Capability[],
  roleId: ProjectRoleId = 'PROJECT_ADMIN',
): void {
  useShellStore.setState({
    principal: {
      actorId: 'actor_fx_01',
      displayName: 'Fixture User',
      roleIds: [roleId],
    },
    sessionToken: 'session_fx_01',
    scope: initialScope,
    scopeKey: makeScopeKey(initialScope),
    scopeChanging: false,
    authorization: {
      scopeKey: makeScopeKey(initialScope),
      roleVersion: 'role_version_fx_01',
      capabilities,
      fetchedAt: '2026-08-12T11:30:00Z',
    },
    authorizationLoading: false,
    authorizationFailed: false,
  });
}

function renderShell(
  props: Partial<PlatformShellProps> = {},
  initialPath = '/dashboard',
): ReturnType<typeof render> {
  return render(
    <ProviderHarness>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<PlatformShell pageAvailability={allPagesAvailable} {...props} />}>
            <Route path="*" element={<div data-testid="route-content">页面内容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

function expectMenuItem(label: string, visible: boolean): void {
  const items = screen.queryAllByRole('menuitem', { name: label });
  if (visible) expect(items.length).toBeGreaterThan(0);
  else expect(items).toHaveLength(0);
}

async function settleAntLayout(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0));
  });
}

describe('platform shell UI contract', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, 'ResizeObserver', {
      configurable: true,
      value: ResizeObserverMock,
    });
    Object.defineProperty(window, 'getComputedStyle', {
      configurable: true,
      value: (element: Element) => browserGetComputedStyle(element),
    });
    installViewport(1440);
    seedShell(CANONICAL_CAPABILITIES);
  });

  afterEach(() => {
    act(() => {
      useShellStore.setState({
        principal: null,
        sessionToken: null,
        scope: null,
        scopeKey: 'unscoped/-/-' as ScopeKey,
        scopeChanging: false,
        authorization: null,
        authorizationLoading: false,
        authorizationFailed: false,
      });
    });
  });

  it('keeps exactly one owner for each of the 21 route patterns', () => {
    const routeOwners = navigationManifest.flatMap((group) =>
      group.items.flatMap((item) =>
        item.activePatterns.map((pattern) => ({ pageId: item.pageId, pattern })),
      ),
    );

    expect(routeOwners).toHaveLength(21);
    expect(new Set(routeOwners.map(({ pattern }) => pattern)).size).toBe(21);
    for (const owner of routeOwners) {
      expect(routeOwners.filter(({ pattern }) => pattern === owner.pattern)).toEqual([owner]);
    }
  });

  it.each([
    {
      name: 'project admin',
      roleId: 'PROJECT_DATA_PROCESSOR' as const,
      capabilities: CANONICAL_CAPABILITIES,
      expected: {
        上传任务: true,
        数据源: true,
        存储容量: true,
        用户权限: true,
      },
    },
    {
      name: 'project developer',
      roleId: 'PROJECT_DEVELOPER' as const,
      capabilities: DEVELOPER_CAPABILITIES,
      expected: {
        上传任务: true,
        数据源: true,
        存储容量: false,
        用户权限: false,
      },
    },
    {
      name: 'data processor',
      roleId: 'PROJECT_DATA_PROCESSOR' as const,
      capabilities: DATA_PROCESSOR_CAPABILITIES,
      expected: {
        上传任务: false,
        数据源: false,
        存储容量: false,
        用户权限: false,
      },
    },
  ])(
    'filters navigation from the $name authorization snapshot',
    async ({ roleId, capabilities, expected }) => {
      seedShell(capabilities, roleId);
      renderShell();
      await settleAntLayout();

      expectMenuItem('工作台', true);
      expectMenuItem('数据集', true);
      expectMenuItem('数据标注', true);
      expectMenuItem('人工问题', true);
      for (const [label, visible] of Object.entries(expected)) expectMenuItem(label, visible);
    },
  );

  it('updates selected navigation and fails closed immediately after revocation or expiry', async () => {
    renderShell({}, '/settings/access');
    await settleAntLayout();

    expect(screen.getByRole('menuitem', { name: '用户权限' })).toHaveClass(
      'ant-menu-item-selected',
    );

    act(() => {
      useShellStore.getState().setAuthorization({
        scopeKey: makeScopeKey(initialScope),
        roleVersion: 'role_version_fx_02',
        capabilities: DATA_PROCESSOR_CAPABILITIES,
        fetchedAt: '2026-08-12T11:35:00Z',
      });
    });
    expectMenuItem('用户权限', false);
    expectMenuItem('数据集', true);

    act(() => {
      useShellStore.getState().setAuthorization({
        scopeKey: makeScopeKey(initialScope),
        roleVersion: 'role_version_fx_03',
        capabilities: CANONICAL_CAPABILITIES,
        fetchedAt: '2026-08-12T11:36:00Z',
        expiresAt: '2026-08-12T11:36:01Z',
      });
    });
    expectMenuItem('数据集', false);
    expect(screen.getByRole('alert')).toHaveTextContent('失败关闭');
  });

  it('executes the scope transaction before installing navigation for the next project', async () => {
    const user = userEvent.setup();
    let resolveAuthorization: ((snapshot: AuthorizationSnapshot) => void) | undefined;
    const authorizationLoader = vi.fn(
      () =>
        new Promise<AuthorizationSnapshot>((resolve) => {
          resolveAuthorization = resolve;
        }),
    );
    const navigationReloader = vi.fn().mockResolvedValue(undefined);
    renderShell({
      authorizationLoader,
      navigationReloader,
      scopeOptions: [
        { ...initialScope, organizationName: 'Org A', projectName: 'Project A' },
        { ...alternateScope, organizationName: 'Org A', projectName: 'Project B' },
      ],
    });

    const projectSelect = screen.getByRole('combobox', { name: '项目' });
    await user.click(projectSelect);
    await user.type(projectSelect, 'Project B');
    await user.click(
      await screen.findByText('Project B', { selector: '.ant-select-item-option-content' }),
    );

    await waitFor(() => {
      expect(authorizationLoader).toHaveBeenCalledWith(alternateScope, expect.any(AbortSignal));
      expect(navigationReloader).toHaveBeenCalledWith(alternateScope, expect.any(AbortSignal));
    });
    expect(screen.getByTestId('route-content').closest('[aria-busy="true"]')).not.toBeNull();
    expect(useShellStore.getState().authorization).toBeNull();

    act(() => {
      resolveAuthorization?.({
        scopeKey: makeScopeKey(alternateScope),
        roleVersion: 'role_version_fx_04',
        capabilities: DEVELOPER_CAPABILITIES,
        fetchedAt: '2026-08-12T11:40:00Z',
      });
    });

    await waitFor(() => {
      expect(useShellStore.getState().scope).toEqual(alternateScope);
      expect(useShellStore.getState().scopeChanging).toBe(false);
      expect(
        screen.getByRole('combobox', { name: '项目' }).closest('.ant-select'),
      ).toHaveTextContent('Project B');
    });
    expectMenuItem('上传任务', true);
    expectMenuItem('存储容量', false);
  });

  it.each([1024, 768])(
    'uses the collapsed keyboard-accessible navigation at %ipx',
    async (width) => {
      installViewport(width);
      renderShell();
      await settleAntLayout();

      expect(screen.getByRole('navigation', { name: '折叠主导航' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '打开导航' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: '作用域：prj_fx_01' })).toBeInTheDocument();
    },
  );

  it('opens the 390px drawer, moves focus inside, and restores focus after Escape', async () => {
    installViewport(390);
    const user = userEvent.setup();
    renderShell({
      scopeOptions: [{ ...initialScope, organizationName: 'Org A', projectName: 'Project A' }],
    });

    const trigger = screen.getByRole('button', { name: '打开导航' });
    expect(screen.queryByRole('navigation', { name: '移动端导航' })).not.toBeInTheDocument();
    await user.click(trigger);

    const drawer = await screen.findByRole('dialog', { name: '导航与作用域' });
    expect(within(drawer).getByRole('navigation', { name: '移动端导航' })).toBeInTheDocument();
    await waitFor(() =>
      expect(within(drawer).getByRole('button', { name: '关闭导航' })).toHaveFocus(),
    );

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
      expect(trigger).toHaveFocus();
    });
  });
});
