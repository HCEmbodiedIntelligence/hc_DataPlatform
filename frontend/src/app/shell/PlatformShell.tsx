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
  FolderKanban,
  HardDrive,
  House,
  ListChecks,
  MapPin,
  Menu as MenuIcon,
  Network,
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

const { Content, Header, Sider } = Layout;
const DatasetContextSelector = lazy(() => import("./DatasetContextSelector"));

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
  P09: <ShieldAlert aria-hidden="true" size={18} strokeWidth={1.8} />,
  P10Q: <ListChecks aria-hidden="true" size={18} strokeWidth={1.8} />,
  P12: <HardDrive aria-hidden="true" size={18} strokeWidth={1.8} />,
  P13: <Clock3 aria-hidden="true" size={18} strokeWidth={1.8} />,
  P14: <Bot aria-hidden="true" size={18} strokeWidth={1.8} />,
  P16: <Crosshair aria-hidden="true" size={18} strokeWidth={1.8} />,
  P17: <Network aria-hidden="true" size={18} strokeWidth={1.8} />,
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
  const projectOptions = [
    ...new Map(
      scopeOptions.flatMap((option) =>
        option.projectId === undefined
          ? []
          : [
              [
                option.projectId,
                {
                  value: option.projectId,
                  label: option.projectName ?? option.projectId,
                },
              ] as const,
            ],
      ),
    ).values(),
  ];
  if (
    scope?.projectId &&
    !projectOptions.some((option) => option.value === scope.projectId)
  ) {
    projectOptions.push({ value: scope.projectId, label: scope.projectId });
  }
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
        option.projectId === scope.projectId && option.projectWide === true,
    );
  const applyManualRegion = () => {
    const regionCode = manualRegion.trim();
    if (!scope || !regionCode || regionCode === scope.regionCode) return;
    onSelect({ ...scope, regionCode });
  };
  if (
    scope?.regionCode &&
    !regionOptions.some((option) => option.value === scope.regionCode)
  ) {
    regionOptions.push({ value: scope.regionCode, label: scope.regionCode });
  }

  return (
    <div aria-label="当前作用域" className={styles.scopePanel} role="group">
      <label className={`${styles.scopeField} ${styles.projectField}`}>
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
          value={scope?.projectId}
          onChange={(projectId: string) => {
            const candidate =
              scopeOptions.find(
                (option) =>
                  option.organizationId === scope?.organizationId &&
                  option.projectId === projectId &&
                  option.regionCode === scope?.regionCode,
              ) ??
              scopeOptions.find(
                (option) =>
                  option.organizationId === scope?.organizationId &&
                  option.projectId === projectId,
              );
            if (candidate !== undefined) onSelect(toScope(candidate));
          }}
        />
      </label>
      <label className={`${styles.scopeField} ${styles.regionField}`}>
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
}: PlatformShellProps) {
  const storedPrincipal = useShellStore((state) => state.principal);
  const storedScope = useShellStore((state) => state.scope);
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
  const location = useLocation();
  const viewportMode = useShellViewportMode();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [navigationCollapsed, setNavigationCollapsed] = useState(
    readNavigationCollapsedPreference,
  );
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null);
  const previousPathnameRef = useRef(location.pathname);
  const principal = e02VisualFixtureEnabled
    ? e02VisualPrincipal
    : storedPrincipal;
  const scope = e02VisualFixtureEnabled ? e02VisualScope : storedScope;
  const availableScopeOptions = e02VisualFixtureEnabled
    ? e02VisualScopeOptions
    : scopeOptions;

  const desktopNavigationCollapsed =
    viewportMode === "desktop" && navigationCollapsed;
  const shellNavigationCollapsed =
    viewportMode === "compact" || desktopNavigationCollapsed;
  const grantedCapabilities = useMemo(
    () =>
      expandGrantedCapabilities(
        capabilitiesLoading || capabilitiesFailed
          ? []
          : (authorization?.capabilities ?? []),
      ),
    [authorization, capabilitiesFailed, capabilitiesLoading, hasCapability],
  );
  const visibleManifest = useMemo(
    () =>
      e02VisualFixtureEnabled
        ? navigationManifest
        : filterNavigationManifest(grantedCapabilities, pageAvailability),
    [grantedCapabilities, pageAvailability],
  );
  const showDatasetSelector = datasetContextKind(location.pathname) !== null;

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

  const closeMobileNavigation = () => {
    setMobileOpen(false);
    globalThis.setTimeout(() => mobileMenuButtonRef.current?.focus(), 0);
  };

  const selectScope = async (next: Scope) => {
    if (e02VisualFixtureEnabled) return;
    if (authorizationLoader === undefined) {
      showToast({
        title: "无法切换作用域",
        message: "授权快照加载器尚未配置",
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
              ?.path ?? null
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

  const scopeSelectors = (
    <ScopeSelectors
      disabled={
        scopeChanging ||
        (authorizationLoader === undefined && !e02VisualFixtureEnabled)
      }
      scope={scope}
      scopeOptions={availableScopeOptions}
      onSelect={(next) => void selectScope(next)}
    />
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
          to="/dashboard"
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

        {viewportMode !== "mobile" ? (
          <Button
            aria-label="全局搜索尚未开放"
            className={styles.globalSearch}
            disabled
            icon={<Search aria-hidden="true" size={17} strokeWidth={1.8} />}
            type="text"
          >
            <span>搜索尚未开放</span>
          </Button>
        ) : null}

        <Space className={styles.headerActions} size={4}>
          {viewportMode !== "mobile" ? (
            <Button
              aria-label="通知尚未开放"
              className={styles.unavailableAction}
              disabled
              icon={<Bell aria-hidden="true" size={18} strokeWidth={1.8} />}
              type="text"
            >
              <span>通知尚未开放</span>
            </Button>
          ) : null}
          <Dropdown
            menu={{
              items: [
                {
                  key: "settings",
                  label: "账户设置（尚未开放）",
                  icon: <Settings aria-hidden="true" />,
                  disabled: true,
                },
                {
                  key: "logout",
                  label: "退出登录（尚未接入）",
                  icon: <UserRound aria-hidden="true" />,
                  disabled: true,
                },
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
          {capabilitiesFailed && !e02VisualFixtureEnabled ? (
            <Alert
              className={styles.authorizationWarning}
              role="alert"
              showIcon
              title="授权快照不可用，当前作用域已按失败关闭处理。"
              type="warning"
            />
          ) : null}
          {e02VisualFixtureEnabled ? <ShellLoadingPage /> : <Outlet />}
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
    </Layout>
  );
}
