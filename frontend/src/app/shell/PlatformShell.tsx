import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Bell,
  Box,
  ChevronDown,
  Database,
  Gauge,
  Globe2,
  HardDrive,
  ListTodo,
  Menu as MenuIcon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Tags,
  UploadCloud,
  UserRound,
  Wrench,
  X,
} from 'lucide-react';
import {
  Alert,
  Avatar,
  Button,
  Drawer,
  Dropdown,
  Layout,
  Menu,
  Popover,
  Select,
  Space,
  Typography,
  type MenuProps,
} from 'antd';
import { Outlet, matchPath, useLocation, useNavigate } from 'react-router-dom';
import type { AuthorizationSnapshot, Capability } from '../../entities/capability';
import { makeScopeKey, type Scope } from '../../entities/scope';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { GlobalJobCenter } from '../../shared/jobs/GlobalJobCenter';
import { useShellStore } from '../../shared/scope/shell-store';
import { useScope } from '../providers/ScopeProvider';
import { useToast } from '../providers/ToastProvider';
import {
  filterNavigationManifest,
  type NavigationGroupId,
  type NavigationManifest,
  type PageAvailability,
} from './navigation-manifest';
import styles from './PlatformShell.module.css';

const { Content, Header, Sider } = Layout;
const { Text } = Typography;

export interface ScopeOption extends Scope {
  organizationName: string;
  projectName?: string;
  regionName?: string;
}

export interface PlatformShellProps {
  scopeOptions?: readonly ScopeOption[];
  pageAvailability?: PageAvailability;
  authorizationLoader?: (scope: Scope, signal: AbortSignal) => Promise<AuthorizationSnapshot>;
  navigationReloader?: (scope: Scope, signal: AbortSignal) => Promise<void>;
}

type ShellViewportMode = 'desktop' | 'compact' | 'mobile';

const noPages: PageAvailability = {};
const tabletQuery = '(min-width: 768px)';
const desktopQuery = '(min-width: 1200px)';

const groupIcons: Readonly<Record<NavigationGroupId, ReactNode>> = {
  dashboard: <Gauge aria-hidden="true" size={19} strokeWidth={1.8} />,
  ingest: <UploadCloud aria-hidden="true" size={19} strokeWidth={1.8} />,
  datasets: <Database aria-hidden="true" size={19} strokeWidth={1.8} />,
  annotation: <Tags aria-hidden="true" size={19} strokeWidth={1.8} />,
  manual: <Wrench aria-hidden="true" size={19} strokeWidth={1.8} />,
  storage: <HardDrive aria-hidden="true" size={19} strokeWidth={1.8} />,
  settings: <Settings aria-hidden="true" size={19} strokeWidth={1.8} />,
};

function BrandMark() {
  return (
    <span aria-hidden="true" className={styles.brandMark}>
      <Box size={23} strokeWidth={2} />
      <span />
    </span>
  );
}

function readViewportMode(): ShellViewportMode {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'desktop';
  }
  if (window.matchMedia(desktopQuery).matches) return 'desktop';
  if (window.matchMedia(tabletQuery).matches) return 'compact';
  return 'mobile';
}

function useShellViewportMode(): ShellViewportMode {
  const [mode, setMode] = useState<ShellViewportMode>(readViewportMode);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const desktopMedia = window.matchMedia(desktopQuery);
    const tabletMedia = window.matchMedia(tabletQuery);
    const update = () => {
      setMode(desktopMedia.matches ? 'desktop' : tabletMedia.matches ? 'compact' : 'mobile');
    };
    desktopMedia.addEventListener('change', update);
    tabletMedia.addEventListener('change', update);
    update();
    return () => {
      desktopMedia.removeEventListener('change', update);
      tabletMedia.removeEventListener('change', update);
    };
  }, []);

  return mode;
}

function NavigationMenu({
  manifest,
  collapsed = false,
  label,
  onNavigate,
}: {
  manifest: NavigationManifest;
  collapsed?: boolean;
  label: string;
  onNavigate?: () => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const activeItem = manifest
    .flatMap((group) => group.items)
    .find((item) =>
      item.activePatterns.some((pattern) =>
        Boolean(matchPath({ path: pattern, end: true }, location.pathname)),
      ),
    );
  const activeGroup = manifest.find((group) =>
    group.items.some((item) => item.pageId === activeItem?.pageId),
  );
  const expandedGroup =
    activeGroup && activeGroup.items.length > 1
      ? activeGroup
      : manifest.find((group) => group.groupId === 'ingest' && group.items.length > 1);
  const defaultOpenKeys = expandedGroup ? [`group:${expandedGroup.groupId}`] : [];
  const itemByPageId = useMemo(
    () => new Map(manifest.flatMap((group) => group.items).map((item) => [item.pageId, item])),
    [manifest],
  );
  const menuItems = useMemo<MenuProps['items']>(
    () =>
      manifest.map((group) => {
        if (group.items.length === 1) {
          const item = group.items[0];
          if (item === undefined) return null;
          return {
            key: item.pageId,
            icon: groupIcons[group.groupId],
            label: <span aria-label={item.label}>{group.label}</span>,
            title: group.label,
          };
        }
        return {
          key: `group:${group.groupId}`,
          icon: groupIcons[group.groupId],
          label: group.label,
          title: group.label,
          children: group.items.map((item) => ({
            key: item.pageId,
            label: item.label,
            title: item.label,
          })),
        };
      }),
    [manifest],
  );

  return (
    <nav aria-label={label} className={styles.navigation}>
      <Menu
        key={`${label}:${collapsed ? 'collapsed' : 'expanded'}:${defaultOpenKeys[0] ?? 'none'}`}
        items={menuItems}
        mode="inline"
        inlineCollapsed={collapsed}
        defaultOpenKeys={collapsed ? [] : defaultOpenKeys}
        selectedKeys={activeItem ? [activeItem.pageId] : []}
        onClick={({ key }) => {
          const item = itemByPageId.get(key);
          if (item === undefined) return;
          void navigate(item.path);
          onNavigate?.();
        }}
      />
    </nav>
  );
}

interface ScopeSelectorPanelProps {
  disabled: boolean;
  scope: Scope | null;
  scopeOptions: readonly ScopeOption[];
  onSelect: (scope: Scope) => void;
}

function toScope(option: ScopeOption): Scope {
  return {
    organizationId: option.organizationId,
    ...(option.projectId === undefined ? {} : { projectId: option.projectId }),
    ...(option.regionCode === undefined ? {} : { regionCode: option.regionCode }),
  };
}

function ScopeSelectorPanel({ disabled, scope, scopeOptions, onSelect }: ScopeSelectorPanelProps) {
  const projectKey = (option: Pick<ScopeOption, 'organizationId' | 'projectId'>) =>
    `${option.organizationId}/${option.projectId ?? '-'}`;
  const projectCandidates = [
    ...new Map(scopeOptions.map((option) => [projectKey(option), option])).values(),
  ];
  const selectedOption = scopeOptions.find(
    (option) => scope !== null && makeScopeKey(option) === makeScopeKey(scope),
  );
  const currentProjectKey = scope === null ? undefined : projectKey(scope);
  const projectOptions = projectCandidates.map((option) => ({
    value: projectKey(option),
    label: option.projectName ?? option.projectId ?? '未选择项目',
  }));
  if (scope !== null && !projectOptions.some((option) => option.value === currentProjectKey)) {
    projectOptions.push({
      value: currentProjectKey ?? '',
      label: scope.projectId ?? '未选择项目',
    });
  }
  const regionCandidates = projectCandidates.filter(
    (option) =>
      option.organizationId === scope?.organizationId &&
      option.projectId === scope?.projectId &&
      option.regionCode !== undefined,
  );
  const regionOptions = [
    ...new Map(
      regionCandidates.map((option) => [
        option.regionCode ?? '',
        {
          value: option.regionCode ?? '',
          label: option.regionName ?? option.regionCode ?? '',
        },
      ]),
    ).values(),
  ];
  if (scope?.regionCode && !regionOptions.some((option) => option.value === scope.regionCode)) {
    regionOptions.push({ value: scope.regionCode, label: scope.regionCode });
  }

  return (
    <div aria-label="当前作用域" className={styles.scopePanel} role="group">
      <label className={`${styles.scopeField} ${styles.projectField}`}>
        <span className={styles.srOnly}>组织与项目</span>
        <Select
          aria-label="项目"
          className={styles.projectSelect}
          disabled={disabled}
          loading={disabled}
          optionFilterProp="label"
          options={projectOptions}
          placeholder="请选择项目"
          showSearch
          value={currentProjectKey}
          onChange={(nextProjectKey: string) => {
            const choices = scopeOptions.filter((option) => projectKey(option) === nextProjectKey);
            const candidate =
              choices.find((option) => option.regionCode === scope?.regionCode) ?? choices[0];
            if (candidate !== undefined) onSelect(toScope(candidate));
          }}
        />
      </label>
      <label className={`${styles.scopeField} ${styles.regionField}`}>
        <Globe2 aria-hidden="true" className={styles.regionIcon} size={16} strokeWidth={1.8} />
        <span className={styles.srOnly}>Region</span>
        <Select
          aria-label="Region"
          disabled={disabled}
          loading={disabled}
          optionFilterProp="label"
          options={regionOptions}
          placeholder="请选择 Region"
          showSearch
          value={scope?.regionCode}
          onChange={(regionCode: string) => {
            const candidate =
              scopeOptions.find(
                (option) =>
                  option.organizationId === scope?.organizationId &&
                  option.projectId === scope?.projectId &&
                  option.regionCode === regionCode,
              ) ?? selectedOption;
            if (candidate !== undefined) onSelect(toScope(candidate));
          }}
        />
      </label>
    </div>
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
  const authorization = useShellStore((state) => state.authorization);
  const scopeChanging = useShellStore((state) => state.scopeChanging);
  const {
    has: hasCapability,
    loading: capabilitiesLoading,
    failed: capabilitiesFailed,
  } = useCapabilities();
  const { switchScope } = useScope();
  const { showToast } = useToast();
  const navigate = useNavigate();
  const viewportMode = useShellViewportMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [navigationCollapsed, setNavigationCollapsed] = useState(false);
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null);

  const grantedCapabilities = useMemo(
    () =>
      new Set<Capability>(
        capabilitiesLoading || capabilitiesFailed
          ? []
          : (authorization?.capabilities.filter((capability) => hasCapability(capability)) ?? []),
      ),
    [authorization, capabilitiesFailed, capabilitiesLoading, hasCapability],
  );
  const visibleManifest = useMemo(
    () => filterNavigationManifest(grantedCapabilities, pageAvailability),
    [grantedCapabilities, pageAvailability],
  );
  const activeScopeLabel = useMemo(() => {
    const selected = scopeOptions.find(
      (option) => scope !== null && makeScopeKey(option) === makeScopeKey(scope),
    );
    return selected?.projectName ?? selected?.projectId ?? scope?.projectId ?? '未选择项目';
  }, [scope, scopeOptions]);
  const activeRegionLabel = useMemo(() => {
    const selected = scopeOptions.find(
      (option) => scope !== null && makeScopeKey(option) === makeScopeKey(scope),
    );
    return selected?.regionName ?? scope?.regionCode ?? '未选择 Region';
  }, [scope, scopeOptions]);

  useEffect(() => {
    if (viewportMode !== 'mobile') setMobileOpen(false);
  }, [viewportMode]);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const timer = globalThis.setTimeout(() => mobileCloseButtonRef.current?.focus(), 0);
    return () => globalThis.clearTimeout(timer);
  }, [mobileOpen]);

  const closeMobileNavigation = () => {
    setMobileOpen(false);
    globalThis.setTimeout(() => mobileMenuButtonRef.current?.focus(), 0);
  };

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

  const scopeSelectors = (
    <ScopeSelectorPanel
      disabled={scopeChanging}
      scope={scope}
      scopeOptions={scopeOptions}
      onSelect={(next) => void selectScope(next)}
    />
  );

  return (
    <Layout
      className={styles.shell}
      data-scope-changing={scopeChanging || undefined}
      data-viewport={viewportMode}
    >
      <Header className={styles.header}>
        {viewportMode === 'mobile' ? (
          <Button
            ref={mobileMenuButtonRef}
            aria-expanded={mobileOpen}
            aria-label="打开导航"
            icon={<MenuIcon aria-hidden="true" />}
            title="打开导航"
            type="text"
            onClick={() => {
              setJobsOpen(false);
              setNotificationsOpen(false);
              setMobileOpen(true);
            }}
          />
        ) : null}

        <Button
          aria-label="返回数据工作台"
          className={styles.brand}
          type="link"
          onClick={() => void navigate('/dashboard')}
        >
          <BrandMark />
          <span className={styles.brandFull}>EI 数据平台</span>
          <span className={styles.brandShort}>EI</span>
        </Button>

        {viewportMode === 'desktop' ? (
          <div className={styles.headerScope}>{scopeSelectors}</div>
        ) : viewportMode === 'compact' ? (
          <Popover
            content={scopeSelectors}
            placement="bottom"
            rootClassName={styles.shellPopover}
            trigger="click"
          >
            <Button className={styles.scopeTrigger}>作用域：{activeScopeLabel}</Button>
          </Popover>
        ) : (
          <Text className={styles.mobileScopeLabel} ellipsis title={activeScopeLabel}>
            {activeScopeLabel}
          </Text>
        )}

        {viewportMode !== 'mobile' ? (
          <Button
            aria-label="全局搜索"
            className={styles.globalSearch}
            icon={<Search aria-hidden="true" size={17} strokeWidth={1.8} />}
            type="text"
            onClick={() =>
              showToast({
                title: '全局搜索尚未开放',
                message: '当前环境没有经过确认的全局搜索接口。',
                tone: 'info',
              })
            }
          >
            <span>搜索数据集、Episode、机器人、任务、场景等</span>
            <kbd>⌘ K</kbd>
          </Button>
        ) : null}

        <Space className={styles.headerActions} size={4}>
          {viewportMode !== 'desktop' ? (
            <Button
              aria-label={`Region：${activeRegionLabel}`}
              className={styles.compactRegion}
              icon={<Globe2 aria-hidden="true" size={17} strokeWidth={1.8} />}
              type="text"
            />
          ) : null}
          <Popover
            content={
              <div className={styles.jobsPanel}>
                <GlobalJobCenter />
              </div>
            }
            open={jobsOpen}
            placement="bottomRight"
            rootClassName={styles.shellPopover}
            trigger="click"
            onOpenChange={(open) => {
              setJobsOpen(open);
              if (open) setNotificationsOpen(false);
            }}
          >
            <Button
              aria-expanded={jobsOpen}
              aria-label="任务中心"
              className={styles.jobButton}
              icon={<ListTodo aria-hidden="true" size={18} strokeWidth={1.8} />}
              title="任务中心"
              type={viewportMode === 'desktop' ? 'default' : 'text'}
            >
              {viewportMode === 'desktop' ? '任务队列' : null}
            </Button>
          </Popover>
          <Popover
            content={
              <section aria-label="通知中心" className={styles.notificationPanel}>
                <strong>通知中心</strong>
                <Text type="secondary">当前环境未提供通知查询接口。</Text>
              </section>
            }
            open={notificationsOpen}
            placement="bottomRight"
            rootClassName={styles.shellPopover}
            trigger="click"
            onOpenChange={(open) => {
              setNotificationsOpen(open);
              if (open) setJobsOpen(false);
            }}
          >
            <Button
              aria-expanded={notificationsOpen}
              aria-label="通知中心"
              className={styles.iconAction}
              icon={<Bell aria-hidden="true" size={18} strokeWidth={1.8} />}
              title="通知中心"
              type="text"
            />
          </Popover>
          <Dropdown
            menu={{
              items: [
                { key: 'settings', label: '账户设置', icon: <Settings aria-hidden="true" /> },
                { key: 'logout', label: '退出登录', icon: <UserRound aria-hidden="true" /> },
              ],
            }}
            placement="bottomRight"
            rootClassName={styles.accountDropdown}
            trigger={['click']}
          >
            <Button aria-label="账户菜单" className={styles.accountButton} type="text">
              <Avatar className={styles.accountAvatar} size={32}>
                {(principal?.displayName ?? '未').trim().slice(0, 1)}
              </Avatar>
              <span className={styles.accountName}>{principal?.displayName ?? '未登录'}</span>
              <ChevronDown aria-hidden="true" size={15} />
            </Button>
          </Dropdown>
        </Space>
      </Header>

      <Layout className={styles.body}>
        {viewportMode !== 'mobile' ? (
          <Sider
            className={styles.sider}
            collapsed={viewportMode === 'compact' || navigationCollapsed}
            collapsedWidth={64}
            theme="light"
            trigger={null}
            width={218}
          >
            <NavigationMenu
              collapsed={viewportMode === 'compact' || navigationCollapsed}
              label={viewportMode === 'compact' ? '折叠主导航' : '主导航'}
              manifest={visibleManifest}
            />
            {viewportMode === 'desktop' ? (
              <Button
                aria-label={navigationCollapsed ? '展开导航' : '折叠导航'}
                className={styles.collapseNavigation}
                icon={
                  navigationCollapsed ? (
                    <PanelLeftOpen aria-hidden="true" size={17} />
                  ) : (
                    <PanelLeftClose aria-hidden="true" size={17} />
                  )
                }
                type="text"
                onClick={() => setNavigationCollapsed((current) => !current)}
              />
            ) : null}
          </Sider>
        ) : null}

        <Content aria-busy={scopeChanging} className={styles.content}>
          {capabilitiesFailed ? (
            <Alert
              className={styles.authorizationWarning}
              role="alert"
              showIcon
              title="授权快照不可用，当前作用域已按失败关闭处理。"
              type="warning"
            />
          ) : null}
          <Outlet />
        </Content>
      </Layout>

      <Drawer
        className={styles.mobileDrawer}
        closable={false}
        destroyOnHidden
        keyboard
        open={mobileOpen}
        placement="left"
        rootClassName={styles.mobileDrawerRoot}
        size="min(360px, 92vw)"
        title="导航与作用域"
        onClose={closeMobileNavigation}
      >
        <Button
          ref={mobileCloseButtonRef}
          aria-label="关闭导航"
          className={styles.mobileClose}
          icon={<X aria-hidden="true" />}
          type="text"
          onClick={closeMobileNavigation}
        >
          关闭
        </Button>
        <div className={styles.mobileScope}>{scopeSelectors}</div>
        <NavigationMenu
          label="移动端导航"
          manifest={visibleManifest}
          onNavigate={closeMobileNavigation}
        />
      </Drawer>
    </Layout>
  );
}
