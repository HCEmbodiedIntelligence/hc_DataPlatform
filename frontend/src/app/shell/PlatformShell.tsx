import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Bell,
  Bot,
  Box,
  ChevronDown,
  ClipboardList,
  Clock3,
  CloudUpload,
  Crosshair,
  Database,
  Download,
  FolderKanban,
  HardDrive,
  House,
  ListChecks,
  LogOut,
  MapPin,
  Menu as MenuIcon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  ShieldAlert,
  ScrollText,
  Tags,
  UserRound,
  X,
} from "lucide-react";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Drawer,
  Dropdown,
  Input,
  Layout,
  Menu,
  Select,
  Space,
  type MenuProps,
} from "antd";
import {
  Link,
  Outlet,
  matchPath,
  useLocation,
  useNavigate,
} from "react-router-dom";
import type { AuthorizationSnapshot } from "../../entities/capability";
import { makeScopeKey, type Scope } from "../../entities/scope";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { expandGrantedCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { useScope } from "../providers/ScopeProvider";
import { useToast } from "../providers/ToastProvider";
import {
  filterNavigationManifest,
  navigationManifest,
  type NavigationManifest,
  type PageAvailability,
} from "./navigation-manifest";
import hangchaLogo from "../../assets/hangcha-logo.png";
import styles from "./PlatformShell.module.css";
import { ShellLoadingPage } from "./ShellLoadingPage";
import { datasetContextKind } from "./dataset-context";
import { useUnreadNotificationCount } from "../../features/notifications/api";

const { Content, Header, Sider } = Layout;
const DatasetContextSelector = lazy(() => import("./DatasetContextSelector"));
const NotificationInbox = lazy(
  () => import("../../features/notifications/NotificationInbox"),
);
const GlobalRobotSearchDialog = lazy(
  () => import("../../features/global-search/GlobalRobotSearchDialog"),
);

export interface ScopeOption extends Scope {
  organizationName: string;
  projectName?: string;
  regionName?: string;
  projectWide?: boolean;
}

export interface PlatformShellProps {
  scopeOptions?: readonly ScopeOption[];
  pageAvailability?: PageAvailability;
  authorizationLoader?: (
    scope: Scope,
    signal: AbortSignal,
  ) => Promise<AuthorizationSnapshot>;
  navigationReloader?: (scope: Scope, signal: AbortSignal) => Promise<void>;
  logoutPending?: boolean;
  onLogout?: () => void;
}

type ShellViewportMode = "desktop" | "compact" | "mobile";

const noPages: PageAvailability = {};
const tabletQuery = "(min-width: 768px)";
const desktopQuery = "(min-width: 1360px)";
const navigationPreferenceKey = "hc-platform-navigation-collapsed";
const e02VisualFixtureEnabled =
  import.meta.env.VITE_E02_VISUAL_FIXTURE === "loading";
const e02VisualScope: Scope = Object.freeze({
  organizationId: "org_e02_visual",
  projectId: "project_e02_visual",
  regionCode: "cn-east-01",
});
const e02VisualScopeOptions: readonly ScopeOption[] = Object.freeze([
  {
    ...e02VisualScope,
    organizationName: "杭叉集团",
    projectName: "双臂采集一期",
    regionName: "华东-01",
  },
]);
const e02VisualPrincipal = Object.freeze({
  actorId: "actor_e02_visual",
  displayName: "张驰",
  roleIds: ["PROJECT_ADMIN"],
});

const pageIcons: Readonly<Record<string, ReactNode>> = {
  P01: <House aria-hidden="true" size={18} strokeWidth={1.8} />,
  P20: <ClipboardList aria-hidden="true" size={18} strokeWidth={1.8} />,
  P02: <Database aria-hidden="true" size={18} strokeWidth={1.8} />,
  P03: <CloudUpload aria-hidden="true" size={18} strokeWidth={1.8} />,
  P05: <Box aria-hidden="true" size={18} strokeWidth={1.8} />,
  P08: <Tags aria-hidden="true" size={18} strokeWidth={1.8} />,
  P21: <Download aria-hidden="true" size={18} strokeWidth={1.8} />,
  P09: <ShieldAlert aria-hidden="true" size={18} strokeWidth={1.8} />,
  P10Q: <ListChecks aria-hidden="true" size={18} strokeWidth={1.8} />,
  P12: <HardDrive aria-hidden="true" size={18} strokeWidth={1.8} />,
  P13: <Clock3 aria-hidden="true" size={18} strokeWidth={1.8} />,
  P14: <Bot aria-hidden="true" size={18} strokeWidth={1.8} />,
  P16: <Crosshair aria-hidden="true" size={18} strokeWidth={1.8} />,
  P18: <UserRound aria-hidden="true" size={18} strokeWidth={1.8} />,
  P19: <ScrollText aria-hidden="true" size={18} strokeWidth={1.8} />,
};

function BrandMark() {
  return (
    <span className={styles.brandMark}>
      <img alt="杭叉集团" height="62" src={hangchaLogo} width="106" />
    </span>
  );
}

function focusMainContent(event: React.MouseEvent<HTMLAnchorElement>) {
  event.preventDefault();
  document.getElementById("main-content")?.focus();
}

function readViewportMode(): ShellViewportMode {
  if (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  ) {
    return "desktop";
  }
  if (window.matchMedia(desktopQuery).matches) return "desktop";
  if (window.matchMedia(tabletQuery).matches) return "compact";
  return "mobile";
}

function useShellViewportMode(): ShellViewportMode {
  const [mode, setMode] = useState<ShellViewportMode>(readViewportMode);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const desktopMedia = window.matchMedia(desktopQuery);
    const tabletMedia = window.matchMedia(tabletQuery);
    const update = () => {
      setMode(
        desktopMedia.matches
          ? "desktop"
          : tabletMedia.matches
            ? "compact"
            : "mobile",
      );
    };
    desktopMedia.addEventListener("change", update);
    tabletMedia.addEventListener("change", update);
    update();
    return () => {
      desktopMedia.removeEventListener("change", update);
      tabletMedia.removeEventListener("change", update);
    };
  }, []);

  return mode;
}

function readNavigationCollapsedPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(navigationPreferenceKey) === "true";
  } catch {
    return false;
  }
}

function persistNavigationCollapsedPreference(collapsed: boolean): void {
  try {
    window.localStorage.setItem(navigationPreferenceKey, String(collapsed));
  } catch {
    // Navigation remains usable when storage is unavailable or blocked.
  }
}

function NavigationMenu({
  manifest,
  id,
  collapsed = false,
  label,
  onNavigate,
}: {
  manifest: NavigationManifest;
  id?: string;
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
  const itemByPageId = useMemo(
    () =>
      new Map(
        manifest
          .flatMap((group) => group.items)
          .map((item) => [item.pageId, item]),
      ),
    [manifest],
  );
  const menuItems = useMemo<MenuProps["items"]>(() => {
    const groups: NonNullable<MenuProps["items"]> = [];
    for (const group of manifest) {
      const items: NonNullable<MenuProps["items"]> = group.items.map(
        (item) => ({
          key: item.pageId,
          icon: pageIcons[item.pageId],
          label: <Link to={item.path}>{item.label}</Link>,
          title: item.label,
        }),
      );
      if (group.groupId === "dashboard") groups.push(...items);
      else {
        groups.push({
          key: `group:${group.groupId}`,
          type: "group" as const,
          label: group.label,
          children: items,
        });
      }
    }
    return groups;
  }, [manifest]);

  return (
    <nav aria-label={label} className={styles.navigation} id={id}>
      <Menu
        key={`${label}:${collapsed ? "collapsed" : "expanded"}`}
        items={menuItems}
        mode="inline"
        inlineCollapsed={collapsed}
        selectedKeys={activeItem ? [activeItem.pageId] : []}
        onClick={({ key, domEvent }) => {
          const item = itemByPageId.get(key);
          if (item === undefined) return;
          onNavigate?.();
          const target = domEvent.target;
          if (target instanceof Element && target.closest("a") !== null) return;
          void navigate(item.path);
        }}
      />
    </nav>
  );
}

interface ScopeSelectorsProps {
  disabled: boolean;
  scope: Scope | null;
  scopeOptions: readonly ScopeOption[];
  onSelect: (scope: Scope) => void;
}

function toScope(option: ScopeOption): Scope {
  return {
    organizationId: option.organizationId,
    ...(option.projectId === undefined ? {} : { projectId: option.projectId }),
    ...(option.regionCode === undefined
      ? {}
      : { regionCode: option.regionCode }),
  };
}

function projectOptionKey(
  scope: Pick<Scope, "organizationId" | "projectId">,
): string {
  return JSON.stringify([scope.organizationId, scope.projectId]);
}

function ScopeSelectors({
  disabled,
  scope,
  scopeOptions,
  onSelect,
}: ScopeSelectorsProps) {
  const [manualRegion, setManualRegion] = useState(scope?.regionCode ?? "");
  useEffect(
    () => setManualRegion(scope?.regionCode ?? ""),
    [scope?.regionCode],
  );
  const selectedOption = scopeOptions.find(
    (option) => scope !== null && makeScopeKey(option) === makeScopeKey(scope),
  );
  const projectCandidates = [
    ...new Map(
      scopeOptions.flatMap((option) =>
        option.projectId === undefined
          ? []
          : [[projectOptionKey(option), option] as const],
      ),
    ).values(),
  ];
  const projectOptions = projectCandidates.map((option) => ({
    value: projectOptionKey(option),
    label: `${option.organizationName} / ${option.projectName ?? option.projectId}`,
  }));
  const selectedProject = projectCandidates.find(
    (option) =>
      option.organizationId === scope?.organizationId &&
      option.projectId === scope?.projectId,
  );
  const regionCandidates = scopeOptions.filter(
    (option) =>
      option.organizationId === scope?.organizationId &&
      option.projectId === scope?.projectId &&
      option.regionCode !== undefined,
  );
  const regionOptions = [
    ...new Map(
      regionCandidates.map((option) => [
        option.regionCode ?? "",
        {
          value: option.regionCode ?? "",
          label: option.regionName ?? option.regionCode ?? "",
        },
      ]),
    ).values(),
  ];
  const acceptsManualRegion =
    scope?.projectId !== undefined &&
    scopeOptions.some(
      (option) =>
        option.organizationId === scope.organizationId &&
        option.projectId === scope.projectId &&
        option.projectWide === true,
    );
  const applyManualRegion = () => {
    const regionCode = manualRegion.trim();
    if (!scope || !regionCode || regionCode === scope.regionCode) return;
    onSelect({ ...scope, regionCode });
  };
  if (
    acceptsManualRegion &&
    scope?.regionCode &&
    !regionOptions.some((option) => option.value === scope.regionCode)
  ) {
    regionOptions.push({ value: scope.regionCode, label: scope.regionCode });
  }

  return (
    <div aria-label="当前作用域" className={styles.scopePanel} role="group">
      <label
        className={`${styles.scopeField} ${styles.projectField}`}
        data-scope-slot="project"
      >
        <FolderKanban
          aria-hidden="true"
          className={styles.scopeIcon}
          size={17}
          strokeWidth={1.8}
        />
        <span className={styles.srOnly}>当前项目</span>
        <Select
          aria-label="当前项目"
          disabled={disabled}
          loading={disabled && scope !== null}
          optionFilterProp="label"
          options={projectOptions}
          placeholder="请选择项目"
          showSearch
          virtual={false}
          value={
            selectedProject ? projectOptionKey(selectedProject) : undefined
          }
          onChange={(selectedProjectKey: string) => {
            const candidate =
              scopeOptions.find(
                (option) =>
                  projectOptionKey(option) === selectedProjectKey &&
                  option.regionCode === scope?.regionCode,
              ) ??
              scopeOptions.find(
                (option) => projectOptionKey(option) === selectedProjectKey,
              );
            if (candidate !== undefined) onSelect(toScope(candidate));
          }}
        />
      </label>
      <label
        className={`${styles.scopeField} ${styles.regionField}`}
        data-scope-slot="region"
      >
        <MapPin
          aria-hidden="true"
          className={styles.scopeIcon}
          size={17}
          strokeWidth={1.8}
        />
        <span className={styles.srOnly}>当前区域</span>
        {acceptsManualRegion && regionOptions.length === 0 ? (
          <Input
            aria-label="当前区域"
            disabled={disabled}
            placeholder="输入区域后回车"
            title="当前项目为项目级授权，服务端未返回区域目录；请输入真实区域代码。"
            value={manualRegion}
            onBlur={applyManualRegion}
            onChange={(event) => setManualRegion(event.target.value)}
            onPressEnter={applyManualRegion}
          />
        ) : (
          <Select
            aria-label="当前区域"
            disabled={disabled}
            loading={disabled && scope !== null}
            optionFilterProp="label"
            options={regionOptions}
            placeholder="请选择区域"
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
              if (candidate !== undefined)
                onSelect({ ...toScope(candidate), regionCode });
            }}
          />
        )}
      </label>
    </div>
  );
}

export function PlatformShell({
  scopeOptions = [],
  pageAvailability = noPages,
  authorizationLoader,
  navigationReloader,
  logoutPending = false,
  onLogout,
}: PlatformShellProps) {
  const storedPrincipal = useShellStore((state) => state.principal);
  const storedScope = useShellStore((state) => state.scope);
  const authorization = useShellStore((state) => state.authorization);
  const platformCapabilities = useShellStore(
    (state) => state.platformCapabilities,
  );
  const scopeChanging = useShellStore((state) => state.scopeChanging);
  const { loading: capabilitiesLoading, failed: capabilitiesFailed } =
    useCapabilities();
  const { switchScope } = useScope();
  const { showToast } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const viewportMode = useShellViewportMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [globalSearchOpen, setGlobalSearchOpen] = useState(false);
  const [navigationCollapsed, setNavigationCollapsed] = useState(
    readNavigationCollapsedPreference,
  );
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null);
  const previousPathnameRef = useRef(location.pathname);
  const useVisualFixture = e02VisualFixtureEnabled && storedPrincipal === null;
  const principal = useVisualFixture ? e02VisualPrincipal : storedPrincipal;
  const scope = useVisualFixture ? e02VisualScope : storedScope;
  const availableScopeOptions = useVisualFixture
    ? e02VisualScopeOptions
    : scopeOptions;
  const unreadNotifications = useUnreadNotificationCount();

  const desktopNavigationCollapsed =
    viewportMode === "desktop" && navigationCollapsed;
  const shellNavigationCollapsed =
    viewportMode === "compact" || desktopNavigationCollapsed;
  const grantedCapabilities = useMemo(() => {
    const expanded = new Set(
      expandGrantedCapabilities([
        ...(capabilitiesLoading || capabilitiesFailed
          ? []
          : (authorization?.capabilities ?? [])),
        ...platformCapabilities,
      ]),
    );
    if (
      platformCapabilities.includes("platform.account.read") ||
      platformCapabilities.includes("platform.account.manage")
    ) {
      expanded.add("access.read");
    }
    if (platformCapabilities.includes("platform.account.manage")) {
      expanded.add("access.manage");
    }
    return expanded;
  }, [
    authorization,
    capabilitiesFailed,
    capabilitiesLoading,
    platformCapabilities,
  ]);
  const visibleManifest = useMemo(() => {
    if (useVisualFixture) return navigationManifest;
    return filterNavigationManifest(grantedCapabilities, pageAvailability);
  }, [grantedCapabilities, pageAvailability, useVisualFixture]);
  const showProjectFeatures = scope?.projectId !== undefined;
  const showDatasetSelector =
    showProjectFeatures && datasetContextKind(location.pathname) !== null;

  useEffect(() => {
    if (viewportMode !== "mobile") setMobileOpen(false);
  }, [viewportMode]);

  useEffect(() => {
    if (previousPathnameRef.current === location.pathname) return undefined;
    previousPathnameRef.current = location.pathname;
    const frame = globalThis.requestAnimationFrame(() => {
      document.getElementById("main-content")?.focus({ preventScroll: true });
    });
    return () => globalThis.cancelAnimationFrame(frame);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const timer = globalThis.setTimeout(
      () => mobileCloseButtonRef.current?.focus(),
      0,
    );
    return () => globalThis.clearTimeout(timer);
  }, [mobileOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k")
        return;
      event.preventDefault();
      if (showProjectFeatures) setGlobalSearchOpen(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showProjectFeatures]);

  const closeMobileNavigation = () => {
    setMobileOpen(false);
    globalThis.setTimeout(() => mobileMenuButtonRef.current?.focus(), 0);
  };

  const selectScope = async (next: Scope) => {
    if (useVisualFixture) return;
    if (authorizationLoader === undefined) {
      showToast({
        title: "无法切换作用域",
        message: "授权状态加载器尚未配置",
        tone: "error",
      });
      return;
    }
    try {
      await switchScope(next, authorizationLoader, {
        ...(navigationReloader ? { reloadNavigation: navigationReloader } : {}),
        resolveLegalPath: (snapshot) => {
          const allowed = new Set(snapshot.capabilities);
          return (
            filterNavigationManifest(allowed, pageAvailability)[0]?.items[0]
              ?.path ?? "/dashboard"
          );
        },
        navigate: (path) => void navigate(path),
      });
    } catch {
      showToast({
        title: "作用域切换失败",
        message: "旧作用域数据已清理，当前授权按失败关闭处理。",
        tone: "error",
      });
    }
  };

  const scopeSelectors =
    showProjectFeatures && availableScopeOptions.length > 0 ? (
      <ScopeSelectors
        disabled={
          scopeChanging ||
          (authorizationLoader === undefined && !useVisualFixture)
        }
        scope={scope}
        scopeOptions={availableScopeOptions}
        onSelect={(next) => void selectScope(next)}
      />
    ) : (
      <div
        aria-label="当前作用域"
        className={`${styles.scopePanel} ${styles.unscopedScopePanel}`}
        role="group"
      >
        <div
          aria-label="当前项目：尚未加入组织或项目"
          className={`${styles.scopeField} ${styles.projectField} ${styles.unscopedScopeField}`}
          data-scope-slot="project"
        >
          <FolderKanban
            aria-hidden="true"
            className={styles.scopeIcon}
            size={17}
            strokeWidth={1.8}
          />
          <span>尚未加入组织或项目</span>
        </div>
        <Link
          className={`${styles.scopeField} ${styles.regionField} ${styles.unscopedScopeField} ${styles.unscopedManageLink}`}
          data-scope-slot="region"
          to="/account/settings?tab=memberships"
        >
          <MapPin
            aria-hidden="true"
            className={styles.scopeIcon}
            size={17}
            strokeWidth={1.8}
          />
          <span>到账户设置管理</span>
        </Link>
      </div>
    );

  return (
    <Layout
      className={styles.shell}
      data-scope-changing={scopeChanging || undefined}
      data-navigation-collapsed={shellNavigationCollapsed || undefined}
      data-viewport={viewportMode}
    >
      <a
        className={styles.skipLink}
        href="#main-content"
        onClick={focusMainContent}
      >
        跳到主要内容
      </a>
      <Header className={styles.header}>
        {viewportMode === "mobile" ? (
          <Button
            ref={mobileMenuButtonRef}
            aria-expanded={mobileOpen}
            aria-label="打开导航"
            icon={<MenuIcon aria-hidden="true" />}
            title="打开导航"
            type="text"
            onClick={() => setMobileOpen(true)}
          />
        ) : null}

        <Link
          aria-label="杭叉集团 HC 数据平台工作台"
          className={`${styles.brand} ${shellNavigationCollapsed ? styles.brandCollapsed : ""}`}
          to="/"
        >
          <BrandMark />
          <span className={styles.brandFull}>HC 数据平台</span>
          <span className={styles.brandShort}>HC</span>
        </Link>

        {viewportMode !== "mobile" ? (
          <div className={styles.headerScope}>{scopeSelectors}</div>
        ) : null}

        {showDatasetSelector && viewportMode !== "mobile" ? (
          <div className={styles.headerDataset}>
            <Suspense
              fallback={
                <span className={styles.contextLoading}>加载数据集…</span>
              }
            >
              <DatasetContextSelector />
            </Suspense>
          </div>
        ) : null}

        {showProjectFeatures && viewportMode !== "mobile" ? (
          <Button
            aria-expanded={globalSearchOpen}
            aria-haspopup="dialog"
            aria-label="全局搜索"
            className={styles.globalSearch}
            icon={<Search aria-hidden="true" size={17} strokeWidth={1.8} />}
            type="text"
            onClick={() => setGlobalSearchOpen(true)}
          >
            <span>搜索机器人</span>
          </Button>
        ) : null}

        <Space className={styles.headerActions} size={4}>
          {viewportMode !== "mobile" ? (
            <Button
              aria-expanded={notificationsOpen}
              aria-haspopup="dialog"
              aria-label={
                unreadNotifications.data && unreadNotifications.data > 0
                  ? `通知，${unreadNotifications.data} 条未读`
                  : "通知"
              }
              className={styles.notificationAction}
              icon={
                <Badge
                  count={unreadNotifications.data}
                  overflowCount={99}
                  size="small"
                >
                  <Bell aria-hidden="true" size={18} strokeWidth={1.8} />
                </Badge>
              }
              type="text"
              onClick={() => setNotificationsOpen(true)}
            >
              <span>通知</span>
            </Button>
          ) : null}
          <Dropdown
            menu={{
              items: [
                {
                  key: "account",
                  label: <Link to="/account">个人主页</Link>,
                  icon: <House aria-hidden="true" />,
                },
                ...(viewportMode === "mobile"
                  ? [
                      {
                        key: "notifications",
                        label: "通知",
                        icon: <Bell aria-hidden="true" />,
                        onClick: () => setNotificationsOpen(true),
                      },
                    ]
                  : []),
                {
                  key: "settings",
                  label: <Link to="/account/settings">账户设置</Link>,
                  icon: <Settings aria-hidden="true" />,
                },
                ...(onLogout
                  ? [
                      {
                        key: "logout",
                        label: logoutPending ? "正在退出…" : "退出登录",
                        icon: <LogOut aria-hidden="true" />,
                        disabled: logoutPending,
                        onClick: onLogout,
                      },
                    ]
                  : []),
              ],
            }}
            placement="bottomRight"
            rootClassName={styles.accountDropdown}
            trigger={["click"]}
          >
            <Button
              aria-label="账户菜单"
              className={styles.accountButton}
              type="text"
            >
              <Avatar className={styles.accountAvatar} size={32}>
                {(principal?.displayName ?? "未").trim().slice(0, 1)}
              </Avatar>
              <span className={styles.accountName}>
                {principal?.displayName ?? "未登录"}
              </span>
              <ChevronDown aria-hidden="true" size={15} />
            </Button>
          </Dropdown>
        </Space>
      </Header>

      <Layout className={styles.body}>
        {viewportMode !== "mobile" ? (
          <Sider
            className={styles.sider}
            collapsed={viewportMode === "compact" || desktopNavigationCollapsed}
            collapsedWidth={64}
            data-navigation-collapsed={shellNavigationCollapsed || undefined}
            theme="light"
            trigger={null}
            width={218}
          >
            <NavigationMenu
              collapsed={viewportMode === "compact" || navigationCollapsed}
              id="platform-primary-navigation"
              label={viewportMode === "compact" ? "折叠主导航" : "主导航"}
              manifest={visibleManifest}
            />
            {viewportMode === "desktop" ? (
              <Button
                aria-label={navigationCollapsed ? "展开导航" : "折叠导航"}
                aria-controls="platform-primary-navigation"
                aria-expanded={!navigationCollapsed}
                className={styles.collapseNavigation}
                icon={
                  navigationCollapsed ? (
                    <PanelLeftOpen aria-hidden="true" size={17} />
                  ) : (
                    <PanelLeftClose aria-hidden="true" size={17} />
                  )
                }
                type="text"
                title={navigationCollapsed ? "展开导航" : "折叠导航"}
                onClick={() =>
                  setNavigationCollapsed((current) => {
                    const next = !current;
                    persistNavigationCollapsedPreference(next);
                    return next;
                  })
                }
              />
            ) : null}
          </Sider>
        ) : null}

        <Content
          aria-busy={scopeChanging}
          className={styles.content}
          id="main-content"
          role="main"
          tabIndex={-1}
        >
          {scope !== null && capabilitiesFailed && !useVisualFixture ? (
            <Alert
              className={styles.authorizationWarning}
              role="alert"
              showIcon
              title="授权状态不可用，当前作用域已按失败关闭处理。"
              type="warning"
            />
          ) : null}
          {useVisualFixture ? <ShellLoadingPage /> : <Outlet />}
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
        title="导航"
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
        <div className={styles.mobileScope}>
          {showDatasetSelector ? (
            <div className={styles.mobileDataset}>
              <span>数据集</span>
              <Suspense
                fallback={
                  <span className={styles.contextLoading}>加载数据集…</span>
                }
              >
                <DatasetContextSelector />
              </Suspense>
            </div>
          ) : null}
          {scopeSelectors}
        </div>
        <NavigationMenu
          label="移动端导航"
          manifest={visibleManifest}
          onNavigate={closeMobileNavigation}
        />
      </Drawer>

      <Drawer
        destroyOnHidden
        open={notificationsOpen}
        placement="right"
        rootClassName={styles.notificationDrawerRoot}
        size="min(448px, 100vw)"
        title="通知"
        onClose={() => setNotificationsOpen(false)}
      >
        <Suspense
          fallback={<span className={styles.contextLoading}>加载通知…</span>}
        >
          <NotificationInbox />
        </Suspense>
      </Drawer>

      {showProjectFeatures && globalSearchOpen ? (
        <Suspense fallback={null}>
          <GlobalRobotSearchDialog
            open={globalSearchOpen}
            onClose={() => setGlobalSearchOpen(false)}
          />
        </Suspense>
      ) : null}
    </Layout>
  );
}
