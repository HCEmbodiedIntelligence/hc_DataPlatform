import { useEffect, useMemo, useRef, useState } from 'react';
import { Bell, BriefcaseBusiness, ChevronDown, Menu, X } from 'lucide-react';
import { Link, Outlet, matchPath, useLocation, useNavigate } from 'react-router-dom';
import type { AuthorizationSnapshot } from '../../entities/capability';
import { makeScopeKey, type Scope } from '../../entities/scope';
import { GlobalJobCenter } from '../../shared/jobs/GlobalJobCenter';
import { useShellStore } from '../../shared/scope/shell-store';
import { trapTabKey } from '../../shared/ui/focus-trap';
import { useScope } from '../providers/ScopeProvider';
import { useToast } from '../providers/ToastProvider';
import {
  filterNavigationManifest,
  type NavigationManifest,
  type PageAvailability,
} from './navigation-manifest';

export interface ScopeOption extends Scope {
  organizationName: string;
  projectName?: string;
  regionName?: string;
}

export interface PlatformShellProps {
  scopeOptions?: readonly ScopeOption[];
  pageAvailability?: PageAvailability;
  authorizationLoader?: (
    scope: Scope,
    signal: AbortSignal,
  ) => Promise<AuthorizationSnapshot>;
  navigationReloader?: (scope: Scope, signal: AbortSignal) => Promise<void>;
}

const noPages: PageAvailability = {};

function NavigationTree({
  manifest,
  onNavigate,
  label,
}: {
  manifest: NavigationManifest;
  onNavigate?: () => void;
  label: string;
}) {
  const location = useLocation();
  return (
    <nav aria-label={label}>
      {manifest.map((group) => (
        <section className="shell-nav__group" key={group.groupId}>
          <h2>{group.label}</h2>
          <ul>
            {group.items.map((item) => {
              const active = item.activePatterns.some((pattern) =>
                Boolean(matchPath({ path: pattern, end: true }, location.pathname)),
              );
              return (
                <li key={item.pageId}>
                  <Link aria-current={active ? 'page' : undefined} to={item.path} onClick={onNavigate}>
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </nav>
  );
}

export function PlatformShell({
  scopeOptions = [],
  pageAvailability = noPages,
  authorizationLoader,
  navigationReloader,
}: PlatformShellProps) {
  const principal = useShellStore((state) => state.principal);
  const scope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scopeChanging = useShellStore((state) => state.scopeChanging);
  const authorization = useShellStore((state) => state.authorization);
  const authorizationFailed = useShellStore((state) => state.authorizationFailed);
  const { switchScope } = useScope();
  const { showToast } = useToast();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null);
  const mobileDrawerRef = useRef<HTMLDivElement>(null);

  const capabilities = useMemo(
    () =>
      new Set(
        authorization?.scopeKey === scopeKey && !authorizationFailed
          ? authorization.capabilities
          : [],
      ),
    [authorization, authorizationFailed, scopeKey],
  );
  const visibleManifest = useMemo(
    () => filterNavigationManifest(capabilities, pageAvailability),
    [capabilities, pageAvailability],
  );

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const menuButton = mobileMenuButtonRef.current;
    mobileCloseButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false);
    };
    globalThis.addEventListener('keydown', closeOnEscape);
    return () => {
      globalThis.removeEventListener('keydown', closeOnEscape);
      menuButton?.focus();
    };
  }, [mobileOpen]);

  const selectScope = async (next: Scope) => {
    if (authorizationLoader === undefined) {
      showToast({ title: '无法切换作用域', message: '授权快照加载器尚未配置', tone: 'error' });
      return;
    }
    try {
      await switchScope(next, authorizationLoader, {
        ...(navigationReloader ? { reloadNavigation: navigationReloader } : {}),
        resolveLegalPath: (snapshot) => {
          const allowed = new Set(snapshot.capabilities);
          return filterNavigationManifest(allowed, pageAvailability)[0]?.items[0]?.path ?? null;
        },
        navigate: (path) => void navigate(path),
      });
    } catch {
      showToast({
        title: '作用域切换失败',
        message: '旧作用域数据已清理，当前授权按失败关闭处理。',
        tone: 'error',
      });
    }
  };

  const organizationIds = [...new Set(scopeOptions.map((option) => option.organizationId))];
  const projectOptions = scopeOptions.filter((option) => option.organizationId === scope?.organizationId);
  const regionOptions = projectOptions.filter((option) => option.projectId === scope?.projectId);

  return (
    <div className="platform-shell" data-scope-changing={scopeChanging || undefined}>
      <header className="platform-shell__header">
        <button
          ref={mobileMenuButtonRef}
          className="icon-button mobile-only"
          type="button"
          title="打开导航"
          aria-label="打开导航"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen(true)}
        >
          <Menu aria-hidden="true" />
        </button>
        <Link className="platform-shell__brand" to="/dashboard">
          具身智能数据平台
        </Link>
        <div className="scope-selectors" aria-label="当前作用域">
          <label>
            <span>组织</span>
            <select
              aria-label="组织"
              disabled={scopeChanging || organizationIds.length === 0}
              value={scope?.organizationId ?? ''}
              onChange={(event) => {
                const candidate = scopeOptions.find((option) => option.organizationId === event.target.value);
                if (candidate) void selectScope(candidate);
              }}
            >
              <option value="">请选择组织</option>
              {organizationIds.map((organizationId) => {
                const option = scopeOptions.find((entry) => entry.organizationId === organizationId);
                return <option key={organizationId} value={organizationId}>{option?.organizationName ?? organizationId}</option>;
              })}
            </select>
          </label>
          <label>
            <span>项目</span>
            <select
              aria-label="项目"
              disabled={scopeChanging || projectOptions.length === 0}
              value={scope?.projectId ?? ''}
              onChange={(event) => {
                const candidate = projectOptions.find((option) => option.projectId === event.target.value);
                if (candidate) void selectScope(candidate);
              }}
            >
              <option value="">请选择项目</option>
              {projectOptions.map((option) => <option key={`${option.organizationId}/${option.projectId ?? '-'}`} value={option.projectId}>{option.projectName ?? option.projectId}</option>)}
            </select>
          </label>
          <label>
            <span>Region</span>
            <select
              aria-label="Region"
              disabled={scopeChanging || regionOptions.length === 0}
              value={scope?.regionCode ?? ''}
              onChange={(event) => {
                const candidate = regionOptions.find((option) => option.regionCode === event.target.value);
                if (candidate) void selectScope(candidate);
              }}
            >
              <option value="">请选择 Region</option>
              {regionOptions.map((option) => <option key={makeScopeKey(option)} value={option.regionCode}>{option.regionName ?? option.regionCode}</option>)}
            </select>
          </label>
        </div>
        <div className="platform-shell__actions">
          <button
            className="icon-button"
            type="button"
            title="任务中心"
            aria-label="任务中心"
            aria-expanded={jobsOpen}
            onClick={() => setJobsOpen((value) => !value)}
          >
            <BriefcaseBusiness aria-hidden="true" />
          </button>
          <button
            className="icon-button"
            type="button"
            title="通知中心"
            aria-label="通知中心"
            aria-expanded={notificationsOpen}
            onClick={() => setNotificationsOpen((value) => !value)}
          >
            <Bell aria-hidden="true" />
          </button>
          <details className="account-menu">
            <summary aria-label="账户菜单">
              <span>{principal?.displayName ?? '未登录'}</span>
              <ChevronDown aria-hidden="true" />
            </summary>
            <div role="menu">
              <button type="button" role="menuitem">账户设置</button>
              <button type="button" role="menuitem">退出登录</button>
            </div>
          </details>
        </div>
      </header>

      <aside className="platform-shell__sidebar desktop-nav">
        <NavigationTree manifest={visibleManifest} label="主导航" />
      </aside>

      <details className="collapsed-nav">
        <summary>导航</summary>
        <div className="collapsed-nav__flyout">
          <NavigationTree manifest={visibleManifest} label="折叠导航" />
        </div>
      </details>

      {mobileOpen ? (
        <div
          ref={mobileDrawerRef}
          className="mobile-drawer"
          role="dialog"
          aria-modal="true"
          aria-label="移动端导航"
          onKeyDown={(event) => trapTabKey(event, mobileDrawerRef)}
        >
          <button
            ref={mobileCloseButtonRef}
            className="icon-button"
            type="button"
            title="关闭导航"
            aria-label="关闭导航"
            onClick={() => setMobileOpen(false)}
          >
            <X aria-hidden="true" />
          </button>
          <NavigationTree manifest={visibleManifest} label="移动端导航" onNavigate={() => setMobileOpen(false)} />
        </div>
      ) : null}

      <aside className="shell-popover" hidden={!jobsOpen}>
        <GlobalJobCenter />
      </aside>
      {notificationsOpen ? (
        <aside className="shell-popover" aria-label="通知中心">
          <h2>通知中心</h2>
          <p>暂无新通知</p>
        </aside>
      ) : null}

      <main className="platform-shell__main" aria-busy={scopeChanging}>
        {authorizationFailed ? (
          <div className="scope-warning" role="alert">授权快照不可用，当前作用域已按失败关闭处理。</div>
        ) : null}
        <Outlet />
      </main>
    </div>
  );
}
