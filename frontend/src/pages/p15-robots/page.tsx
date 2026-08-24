import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
} from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { Box, Boxes, Radio, Search, ShieldCheck } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import type { ComponentTreeNode } from "../../entities/component";
import { routes as calibrationRoutes } from "../../features/calibrations/routing";
import { routes as dataSchemaRoutes } from "../../features/data-schemas/routing";
import {
  useComponentChannels,
  useComponentFrames,
  useCreateRobot,
  useCreateRobotComponent,
  useCreateRobotMaintenanceRecord,
  useRobotBootstrap,
  useRobotComponents,
  useRobotMaintenanceRecords,
  useRobots,
  useTransitionRobotLifecycle,
  useTransitionRobotComponentLifecycle,
  useUpdateRobot,
  useUpdateRobotComponent,
} from "../../features/robots/api";
import { buildComponentTree } from "../../features/robots/constraints";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  DataCursorPager,
  DataTable,
  DetailTabs,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from "../../shared/ui";
import workspace from "../ui-011e/workspace.module.css";
import { robotsQueryCodec, type RobotsSearch } from "./query-codec";

type LifecycleFilter = "all" | NonNullable<RobotsSearch["lifecycleStatus"]>;
type ConnectivityFilter =
  | "all"
  | NonNullable<RobotsSearch["connectivityState"]>;

type RobotRow = NonNullable<
  ReturnType<typeof useRobots>["data"]
>["items"][number];

type RobotDialog =
  | "create"
  | "edit"
  | "transition"
  | "maintenance"
  | "component-create"
  | "component-edit"
  | "component-transition"
  | null;

interface CreateRobotFormValues {
  readonly displayName: string;
  readonly serialNo: string;
  readonly lifecycleStatus: "DRAFT" | "ACTIVE" | "MAINTENANCE";
  readonly connectivityState: "ONLINE" | "OFFLINE" | "DEGRADED";
  readonly connectivitySource?: string;
  readonly connectivityReasonCode?: string;
}

interface EditRobotFormValues {
  readonly displayName: string;
  readonly connectivityState: "ONLINE" | "OFFLINE" | "DEGRADED";
  readonly connectivitySource?: string;
  readonly connectivityReasonCode?: string;
}

interface TransitionRobotFormValues {
  readonly lifecycleStatus: "ACTIVE" | "MAINTENANCE" | "DISABLED" | "RETIRED";
  readonly reason: string;
}

interface MaintenanceRecordFormValues {
  readonly summary: string;
  readonly details?: string;
}

interface CreateComponentFormValues {
  readonly parentComponentId?: string;
  readonly componentModelId: string;
  readonly componentType: string;
  readonly displayName: string;
  readonly serialNo: string;
  readonly lifecycleStatus: "DRAFT" | "ACTIVE" | "MAINTENANCE";
  readonly sortOrder: number;
}

interface EditComponentFormValues {
  readonly parentComponentId?: string;
  readonly componentModelId: string;
  readonly componentType: string;
  readonly displayName: string;
  readonly serialNo: string;
  readonly sortOrder: number;
}

interface TransitionComponentFormValues {
  readonly lifecycleStatus: "ACTIVE" | "MAINTENANCE" | "DISABLED" | "RETIRED";
  readonly reason: string;
}

const detailTabs = [
  { id: "overview", label: "基本信息" },
  { id: "frames", label: "坐标系" },
  { id: "channels", label: "Channel" },
  { id: "history", label: "维护记录" },
] as const;

function SummaryItem({
  icon,
  label,
  value,
}: Readonly<{ icon: React.ReactNode; label: string; value: string }>) {
  return (
    <section className={workspace.summaryItem} aria-label={label}>
      <span className={workspace.summaryIcon}>{icon}</span>
      <span className={workspace.summaryCopy}>
        <span>{label}</span>
        <strong>{value}</strong>
      </span>
    </section>
  );
}

function flattenVisible(
  nodes: readonly ComponentTreeNode[],
  expanded: ReadonlySet<string>,
  depth = 1,
): readonly { readonly node: ComponentTreeNode; readonly depth: number }[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(expanded.has(node.id)
      ? flattenVisible(node.children, expanded, depth + 1)
      : []),
  ]);
}

function AccessibleComponentTree({
  roots,
  selectedId,
  onSelect,
}: Readonly<{
  roots: readonly ComponentTreeNode[];
  selectedId?: string;
  onSelect: (id: string) => void;
}>) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(roots.map((root) => root.id)),
  );
  const visible = useMemo(
    () => flattenVisible(roots, expanded),
    [expanded, roots],
  );
  const [focusedId, setFocusedId] = useState(
    selectedId ?? visible[0]?.node.id ?? "",
  );
  const refs = useRef(new Map<string, HTMLElement>());

  const focusAt = (index: number) => {
    const item = visible[Math.max(0, Math.min(index, visible.length - 1))];
    if (!item) return;
    setFocusedId(item.node.id);
    queueMicrotask(() => refs.current.get(item.node.id)?.focus());
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>, id: string) => {
    const index = visible.findIndex((item) => item.node.id === id);
    const item = visible[index];
    if (!item) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusAt(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusAt(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusAt(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusAt(visible.length - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      if (item.node.children.length > 0 && !expanded.has(id))
        setExpanded(new Set([...expanded, id]));
      else if (item.node.children.length > 0) focusAt(index + 1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (expanded.has(id)) {
        const next = new Set(expanded);
        next.delete(id);
        setExpanded(next);
      } else if (item.node.parentComponentId)
        refs.current.get(item.node.parentComponentId)?.focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
    }
  };

  return (
    <div className={workspace.tree} role="tree" aria-label="组件拓扑">
      {visible.map(({ node, depth }) => (
        <div
          className={workspace.treeItem}
          role="treeitem"
          key={node.id}
          aria-level={depth}
          aria-selected={node.id === selectedId}
          aria-expanded={
            node.children.length ? expanded.has(node.id) : undefined
          }
        >
          <Button
            className={`${workspace.treeButton} ${workspace[`treeLevel${Math.min(depth, 4)}` as keyof typeof workspace]} ${node.id === selectedId ? workspace.treeSelected : ""}`}
            ref={(element) => {
              if (element) refs.current.set(node.id, element);
              else refs.current.delete(node.id);
            }}
            type="text"
            tabIndex={node.id === focusedId ? 0 : -1}
            onFocus={() => setFocusedId(node.id)}
            onKeyDown={(event) => onKeyDown(event, node.id)}
            onClick={() => onSelect(node.id)}
          >
            <span aria-hidden="true">
              {node.children.length ? (expanded.has(node.id) ? "▾" : "▸") : "•"}
            </span>
            {node.displayName}
          </Button>
        </div>
      ))}
    </div>
  );
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = robotsQueryCodec.parse(params);
  const [dialog, setDialog] = useState<RobotDialog>(null);
  const [createForm] = Form.useForm<CreateRobotFormValues>();
  const [editForm] = Form.useForm<EditRobotFormValues>();
  const [transitionForm] = Form.useForm<TransitionRobotFormValues>();
  const [maintenanceForm] = Form.useForm<MaintenanceRecordFormValues>();
  const [createComponentForm] = Form.useForm<CreateComponentFormValues>();
  const [editComponentForm] = Form.useForm<EditComponentFormValues>();
  const [transitionComponentForm] =
    Form.useForm<TransitionComponentFormValues>();
  const [query, setQuery] = useState(search.q ?? "");
  const [lifecycleStatus, setLifecycleStatus] = useState<LifecycleFilter>(
    search.lifecycleStatus ?? "all",
  );
  const [connectivityState, setConnectivityState] =
    useState<ConnectivityFilter>(search.connectivityState ?? "all");
  const robots = useRobots({
    ...(search.q ? { q: search.q } : {}),
    ...(search.lifecycleStatus
      ? { lifecycle_status: search.lifecycleStatus }
      : {}),
    ...(search.connectivityState
      ? { connectivity_state: search.connectivityState }
      : {}),
  });
  const bootstrap = useRobotBootstrap(search.robotId ?? null);
  const components = useRobotComponents(search.robotId ?? null);
  const maintenanceRecords = useRobotMaintenanceRecords(
    search.tab === "history" ? (search.robotId ?? null) : null,
  );
  const frames = useComponentFrames(search.componentId ?? null);
  const channels = useComponentChannels(search.componentId ?? null);
  const createRobot = useCreateRobot();
  const updateRobot = useUpdateRobot();
  const transitionRobot = useTransitionRobotLifecycle();
  const createMaintenanceRecord = useCreateRobotMaintenanceRecord();
  const createRobotComponent = useCreateRobotComponent();
  const updateRobotComponent = useUpdateRobotComponent();
  const transitionRobotComponent = useTransitionRobotComponentLifecycle();
  const capabilities = useCapabilities();
  const canManage = capabilities.has("robot.manage");
  const robotItems = robots.data?.items ?? [];
  const selectedRobot =
    robotItems.find((robot) => robot.id === search.robotId) ?? robotItems[0];
  const tree = buildComponentTree(components.data?.items ?? []);
  const selected = components.data?.items.find(
    (component) => component.id === search.componentId,
  );
  const calibrationReference = frames.data?.items[0];
  const schemaReference = channels.data?.items[0];
  const selectedBootstrap = bootstrap.data;
  const mutationError =
    createRobot.error ??
    updateRobot.error ??
    transitionRobot.error ??
    createMaintenanceRecord.error ??
    createRobotComponent.error ??
    updateRobotComponent.error ??
    transitionRobotComponent.error;

  useEffect(() => {
    if (dialog !== "edit" || !selectedBootstrap) return;
    editForm.setFieldsValue({
      displayName: selectedBootstrap.displayName,
      connectivityState:
        selectedBootstrap.connectivity.state === "UNKNOWN"
          ? "OFFLINE"
          : selectedBootstrap.connectivity.state,
      connectivitySource: selectedBootstrap.connectivity.source ?? undefined,
      connectivityReasonCode:
        selectedBootstrap.connectivity.reasonCode ?? undefined,
    });
  }, [dialog, editForm, selectedBootstrap]);

  useEffect(() => {
    if (dialog !== "component-edit" || !selected) return;
    editComponentForm.setFieldsValue({
      parentComponentId: selected.parentComponentId ?? undefined,
      componentModelId: selected.componentModelId,
      componentType: selected.componentType,
      displayName: selected.displayName,
      serialNo: selected.serialNo,
      sortOrder: Number(selected.sortOrder),
    });
  }, [dialog, editComponentForm, selected]);

  const selectRobot = (robotId: string) => {
    setParams(
      robotsQueryCodec.build(
        { ...search, robotId, componentId: undefined, tab: "overview" },
        search,
      ),
    );
  };

  const submitCreate = (values: CreateRobotFormValues) => {
    createRobot.mutate(
      { ...values, idempotencyKey: crypto.randomUUID() },
      {
        onSuccess: (robot) => {
          setDialog(null);
          createForm.resetFields();
          selectRobot(robot.id);
        },
      },
    );
  };

  const submitEdit = (values: EditRobotFormValues) => {
    if (!selectedBootstrap) return;
    updateRobot.mutate(
      {
        robotId: selectedBootstrap.id,
        etag: selectedBootstrap.etag,
        ...values,
        idempotencyKey: crypto.randomUUID(),
      },
      { onSuccess: () => setDialog(null) },
    );
  };

  const submitTransition = (values: TransitionRobotFormValues) => {
    if (!selectedBootstrap) return;
    transitionRobot.mutate(
      {
        robotId: selectedBootstrap.id,
        etag: selectedBootstrap.etag,
        ...values,
        idempotencyKey: crypto.randomUUID(),
      },
      { onSuccess: () => setDialog(null) },
    );
  };

  const submitMaintenance = (values: MaintenanceRecordFormValues) => {
    if (!selectedBootstrap) return;
    createMaintenanceRecord.mutate(
      {
        robotId: selectedBootstrap.id,
        ...values,
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: () => {
          maintenanceForm.resetFields();
          setDialog(null);
        },
      },
    );
  };

  const submitCreateComponent = (values: CreateComponentFormValues) => {
    if (!selectedBootstrap) return;
    createRobotComponent.mutate(
      {
        robotId: selectedBootstrap.id,
        etag: selectedBootstrap.etag,
        parentComponentId: values.parentComponentId?.trim() || null,
        componentModelId: values.componentModelId,
        componentType: values.componentType,
        displayName: values.displayName,
        serialNo: values.serialNo,
        lifecycleStatus: values.lifecycleStatus,
        sortOrder: values.sortOrder,
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: (result) => {
          createComponentForm.resetFields();
          setDialog(null);
          setParams(
            robotsQueryCodec.build(
              { ...search, componentId: result.component.id, tab: "overview" },
              search,
            ),
          );
        },
      },
    );
  };

  const submitEditComponent = (values: EditComponentFormValues) => {
    if (!selected || !selectedBootstrap) return;
    updateRobotComponent.mutate(
      {
        componentId: selected.id,
        etag: selectedBootstrap.etag,
        parentComponentId: values.parentComponentId?.trim() || null,
        componentModelId: values.componentModelId,
        componentType: values.componentType,
        displayName: values.displayName,
        serialNo: values.serialNo,
        sortOrder: values.sortOrder,
        idempotencyKey: crypto.randomUUID(),
      },
      { onSuccess: () => setDialog(null) },
    );
  };

  const submitTransitionComponent = (values: TransitionComponentFormValues) => {
    if (!selected || !selectedBootstrap) return;
    transitionRobotComponent.mutate(
      {
        componentId: selected.id,
        etag: selectedBootstrap.etag,
        lifecycleStatus: values.lifecycleStatus,
        reason: values.reason,
        idempotencyKey: crypto.randomUUID(),
      },
      { onSuccess: () => setDialog(null) },
    );
  };

  const columns = useMemo<ColumnDef<RobotRow, unknown>[]>(
    () => [
      {
        id: "displayName",
        header: "名称",
        size: 145,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() => selectRobot(row.original.id)}
          >
            {row.original.displayName}
          </Button>
        ),
      },
      {
        id: "serialNo",
        header: "序列号",
        size: 145,
        cell: ({ row }) => row.original.serialNo,
      },
      {
        id: "connectivity",
        header: "在线状态",
        size: 95,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.connectivity}
            label={
              row.original.connectivity === "ONLINE"
                ? "在线"
                : row.original.connectivity
            }
            tone={
              row.original.connectivity === "ONLINE" ? "success" : "warning"
            }
          />
        ),
      },
      {
        id: "lifecycle",
        header: "生命周期",
        size: 100,
        cell: ({ row }) => row.original.lifecycle,
      },
    ],
    [selectRobot],
  );

  const pageState = robots.isPending ? (
    <PageState state="loading" label="机器人与组件" />
  ) : robots.error && isDomainError(robots.error) ? (
    <PageState
      state={robots.error.httpStatus === 403 ? "forbidden" : "error"}
      onRetry={() => void robots.refetch()}
    />
  ) : null;

  return (
    <main className={workspace.page} data-page-id="P15">
      <StandardPageScaffold
        header={{
          title: "机器人与组件",
          description:
            "管理机器人实例、组件拓扑以及固定 Frame、Calibration 与 Schema 引用。",
          breadcrumbs: [
            {
              key: "settings",
              label: "系统管理",
              to: "/settings/robot-models",
            },
            { key: "robots", label: "机器人与组件" },
          ],
          actions: (
            <>
              <Button
                type="primary"
                onClick={() => {
                  createForm.resetFields();
                  createForm.setFieldsValue({
                    lifecycleStatus: "DRAFT",
                    connectivityState: "OFFLINE",
                  });
                  setDialog("create");
                }}
                disabled={!canManage}
              >
                新建机器人
              </Button>
              <Button
                disabled={!selectedBootstrap || !canManage}
                onClick={() => {
                  if (!selectedBootstrap) return;
                  createComponentForm.resetFields();
                  createComponentForm.setFieldsValue({
                    parentComponentId: selected?.id,
                    lifecycleStatus: "DRAFT",
                    sortOrder: components.data?.items.length ?? 0,
                  });
                  setDialog("component-create");
                }}
              >
                添加组件
              </Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Boxes size={20} />}
              label="机器人实例"
              value={String(robotItems.length)}
            />
            <SummaryItem
              icon={<Radio size={20} />}
              label="在线"
              value={String(
                robotItems.filter((item) => item.connectivity === "ONLINE")
                  .length,
              )}
            />
            <SummaryItem
              icon={<Box size={20} />}
              label="已加载组件"
              value={String(components.data?.items.length ?? 0)}
            />
            <SummaryItem
              icon={<ShieldCheck size={20} />}
              label="关系语义"
              value="[from, to)"
            />
          </div>
        }
        filters={
          <FilterToolbar
            onApply={() =>
              setParams(
                robotsQueryCodec.build(
                  {
                    ...search,
                    q: query || undefined,
                    lifecycleStatus:
                      lifecycleStatus === "all" ? undefined : lifecycleStatus,
                    connectivityState:
                      connectivityState === "all"
                        ? undefined
                        : connectivityState,
                    after: undefined,
                    before: undefined,
                  },
                  search,
                ),
              )
            }
            onReset={() => {
              setQuery("");
              setLifecycleStatus("all");
              setConnectivityState("all");
              setParams(
                robotsQueryCodec.build(
                  {
                    ...search,
                    q: undefined,
                    lifecycleStatus: undefined,
                    connectivityState: undefined,
                    after: undefined,
                    before: undefined,
                  },
                  search,
                ),
              );
            }}
          >
            <label className={workspace.toolbarField}>
              <span>机器人名称</span>
              <Input.Search
                className={workspace.toolbarSearch}
                value={query}
                placeholder="搜索机器人"
                enterButton={
                  <Button
                    aria-label="搜索机器人"
                    icon={<Search aria-hidden="true" size={15} />}
                  />
                }
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>生命周期</span>
              <Select
                value={lifecycleStatus}
                options={[
                  { value: "all", label: "全部生命周期" },
                  { value: "DRAFT", label: "草稿" },
                  { value: "ACTIVE", label: "启用" },
                  { value: "MAINTENANCE", label: "维护中" },
                  { value: "DISABLED", label: "已停用" },
                  { value: "RETIRED", label: "已退役" },
                ]}
                onChange={setLifecycleStatus}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>连接状态</span>
              <Select
                value={connectivityState}
                options={[
                  { value: "all", label: "全部连接状态" },
                  { value: "ONLINE", label: "在线" },
                  { value: "OFFLINE", label: "离线" },
                  { value: "DEGRADED", label: "降级" },
                ]}
                onChange={setConnectivityState}
              />
            </label>
          </FilterToolbar>
        }
        state={pageState}
      >
        <div className={workspace.threePane}>
          <section className={workspace.pane} aria-label="机器人实例列表">
            <header className={workspace.paneHeader}>
              <div>
                <h2>机器人实例</h2>
                <p>稳定 ID 与服务端连接事实</p>
              </div>
              <span className={workspace.inlineMeta}>
                共 {robotItems.length} 项
              </span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={robotItems}
                columns={columns}
                getRowId={(robot) => robot.id}
                caption="机器人列表"
                state={robotItems.length ? "ready" : "empty"}
                empty={
                  <PageState state={search.q ? "filtered-empty" : "empty"} />
                }
              />
            </div>
            {robots.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
                  pageInfo={{
                    startCursor: robots.data.pageInfo.start_cursor,
                    endCursor: robots.data.pageInfo.end_cursor,
                    hasPreviousPage: robots.data.pageInfo.has_previous_page,
                    hasNextPage: robots.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(
                      robotsQueryCodec.build({ ...search, ...cursor }, search),
                    )
                  }
                  windowLabel={`当前 ${robotItems.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <section
            className={workspace.pane}
            aria-label="组件拓扑面板"
            aria-busy={components.isFetching}
          >
            <header className={workspace.paneHeader}>
              <div>
                <h2>组件拓扑</h2>
                <p>{selectedRobot?.displayName ?? "选择机器人"}</p>
              </div>
              <Button
                type="text"
                onClick={() => void components.refetch()}
                disabled={!search.robotId}
              >
                刷新
              </Button>
            </header>
            <div className={workspace.paneBody}>
              {tree.diagnostics.map((diagnostic, index) => (
                <p
                  className={workspace.warningNote}
                  role="alert"
                  key={`${diagnostic.kind}-${index}`}
                >
                  {diagnostic.kind}
                  ：服务端返回了不完整的组件拓扑；请刷新后再试，
                  不会依据该本地诊断写入。
                </p>
              ))}
              {tree.roots.length ? (
                <AccessibleComponentTree
                  roots={tree.roots}
                  selectedId={search.componentId}
                  onSelect={(componentId) =>
                    setParams(
                      robotsQueryCodec.build(
                        { ...search, componentId },
                        search,
                      ),
                    )
                  }
                />
              ) : search.robotId && components.isError ? (
                <div className={workspace.pageStateCompact}>
                  <PageState
                    state="feature-unavailable"
                    title="组件拓扑暂时不可用"
                    description="服务端未返回当前机器人已授权的组件；不会生成示意组件。"
                  />
                </div>
              ) : (
                <div className={workspace.visualStage}>
                  <div className={workspace.visualStageCopy}>
                    <Boxes aria-hidden="true" size={44} />
                    <strong>
                      {selectedRobot?.displayName ?? "选择机器人"}
                    </strong>
                    <span>
                      选择固定 robotId
                      后加载授权组件树；键盘支持方向键、Home、End、Enter 与
                      Space。
                    </span>
                  </div>
                </div>
              )}
            </div>
          </section>

          <aside className={workspace.inspector} aria-label="机器人组件详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>
                  {selected?.displayName ??
                    selectedRobot?.displayName ??
                    "组件详情"}
                </h2>
                <p>{selected ? selected.componentType : "机器人列表事实"}</p>
              </div>
              <StatusTag
                status={
                  selected?.lifecycle ??
                  selectedRobot?.connectivity ??
                  "UNKNOWN"
                }
                label={
                  selected?.lifecycle ??
                  (selectedRobot?.connectivity === "ONLINE"
                    ? "在线"
                    : selectedRobot?.connectivity)
                }
                tone={
                  selected?.lifecycle === "ACTIVE" ||
                  selectedRobot?.connectivity === "ONLINE"
                    ? "success"
                    : "neutral"
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>稳定 ID</dt>
                  <dd>
                    <code>{selected?.id ?? selectedRobot?.id ?? "—"}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>序列号</dt>
                  <dd>
                    {selected?.serialNo ?? selectedRobot?.serialNo ?? "—"}
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>父组件</dt>
                  <dd>
                    <code>
                      {selected?.parentComponentId ?? "根节点 / 未加载"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>模型引用</dt>
                  <dd>
                    <code>
                      {selected?.componentModelId ??
                        bootstrap.data?.effectiveModelBinding
                          ?.robotModelVersionId ??
                        "需 Bootstrap 授权"}
                    </code>
                  </dd>
                </div>
              </dl>
              <DetailTabs
                tabs={detailTabs}
                activeTab={search.tab}
                panelIdForTab={(tabId) => `p15-tabpanel-${tabId}`}
                onChange={(tab) =>
                  setParams(
                    robotsQueryCodec.build(
                      { ...search, tab: tab as typeof search.tab },
                      search,
                    ),
                  )
                }
              />
              <section
                className={workspace.tabContent}
                role="tabpanel"
                id="p15-tabpanel-overview"
                aria-labelledby="tab-overview"
                hidden={search.tab !== "overview"}
              >
                <p className={workspace.safeNote}>
                  关系区间统一使用 <code>[validFrom, validTo)</code>
                  ；本地重叠只作预校验，409/422 始终以服务端事实为准。
                </p>
                {selectedBootstrap ? (
                  <Space wrap className={workspace.actionRow}>
                    <Button
                      onClick={() => setDialog("edit")}
                      disabled={
                        !canManage ||
                        !selectedBootstrap.allowedActions.includes("EDIT")
                      }
                    >
                      编辑机器人
                    </Button>
                    <Button
                      onClick={() => {
                        transitionForm.resetFields();
                        setDialog("transition");
                      }}
                      disabled={
                        !canManage ||
                        !selectedBootstrap.allowedActions.includes(
                          "TRANSITION",
                        ) ||
                        selectedBootstrap.lifecycle === "RETIRED"
                      }
                    >
                      变更状态
                    </Button>
                  </Space>
                ) : null}
                {selected && selectedBootstrap ? (
                  <Space wrap className={workspace.actionRow}>
                    <Button
                      onClick={() => setDialog("component-edit")}
                      disabled={!canManage}
                    >
                      编辑组件
                    </Button>
                    <Button
                      onClick={() => {
                        transitionComponentForm.resetFields();
                        setDialog("component-transition");
                      }}
                      disabled={!canManage || selected.lifecycle === "RETIRED"}
                    >
                      变更组件状态
                    </Button>
                  </Space>
                ) : null}
              </section>
              <section
                className={workspace.tabContent}
                role="tabpanel"
                id="p15-tabpanel-frames"
                aria-labelledby="tab-frames"
                hidden={search.tab !== "frames"}
              >
                <p className={workspace.featureNote}>
                  Frame 列表：{frames.data?.items.length ?? 0}
                  ；未授权时保持空，不推断坐标系。
                </p>
              </section>
              <section
                className={workspace.tabContent}
                role="tabpanel"
                id="p15-tabpanel-channels"
                aria-labelledby="tab-channels"
                hidden={search.tab !== "channels"}
              >
                <p className={workspace.featureNote}>
                  Channel 列表：{channels.data?.items.length ?? 0}；Schema
                  版本必须是固定引用。
                </p>
              </section>
              <section
                className={workspace.tabContent}
                role="tabpanel"
                id="p15-tabpanel-history"
                aria-labelledby="tab-history"
                hidden={search.tab !== "history"}
              >
                {maintenanceRecords.isPending ? (
                  <PageState state="loading" label="维护记录" />
                ) : maintenanceRecords.isError ? (
                  <PageState
                    state={
                      isDomainError(maintenanceRecords.error) &&
                      maintenanceRecords.error.httpStatus === 403
                        ? "forbidden"
                        : "error"
                    }
                    onRetry={() => void maintenanceRecords.refetch()}
                  />
                ) : maintenanceRecords.data?.items.length ? (
                  <ol className={workspace.factList} aria-label="维护记录">
                    {maintenanceRecords.data.items.map((record) => (
                      <li className={workspace.factRow} key={record.id}>
                        <span>
                          {record.event_type === "LIFECYCLE_TRANSITION"
                            ? "状态流转"
                            : "维护"}
                        </span>
                        <strong>{record.summary}</strong>
                        <small>
                          {new Date(record.occurred_at).toLocaleString("zh-CN")}
                        </small>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <PageState
                    state="empty"
                    title="尚无维护记录"
                    description="记录检查、保养或现场处置，供后续排查与审计使用。"
                  />
                )}
                <Button
                  type="primary"
                  onClick={() => {
                    maintenanceForm.resetFields();
                    setDialog("maintenance");
                  }}
                  disabled={!selectedBootstrap || !canManage}
                >
                  新增维护记录
                </Button>
              </section>
              <div className={workspace.actionRow}>
                {calibrationReference ? (
                  <Button
                    href={calibrationRoutes.calibrations.build({
                      robotId: selected?.robotId ?? selectedRobot?.id ?? "",
                      componentId: selected?.id ?? "",
                      setId: calibrationReference.calibration_set_id,
                    })}
                  >
                    查看标定
                  </Button>
                ) : (
                  <Button disabled>标定管理</Button>
                )}
                {schemaReference ? (
                  <Button
                    href={dataSchemaRoutes.dataSchemas.build({
                      schemaId: schemaReference.schema_id,
                      schemaVersion: schemaReference.schema_version,
                      componentId: selected?.id ?? "",
                      detailTab: "references",
                    })}
                  >
                    查看 Schema
                  </Button>
                ) : (
                  <Button disabled>Data Schema</Button>
                )}
              </div>
            </div>
          </aside>
        </div>
      </StandardPageScaffold>
      <Modal
        open={dialog === "create"}
        title="新建机器人"
        destroyOnHidden
        onCancel={() => {
          if (!createRobot.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<CreateRobotFormValues>
          form={createForm}
          layout="vertical"
          onFinish={submitCreate}
          requiredMark="optional"
        >
          <Form.Item
            name="displayName"
            label="显示名称"
            rules={[
              {
                required: true,
                whitespace: true,
                max: 256,
                message: "请输入 1–256 个字符的名称",
              },
            ]}
          >
            <Input autoFocus autoComplete="off" maxLength={256} />
          </Form.Item>
          <Form.Item
            name="serialNo"
            label="序列号"
            rules={[
              {
                required: true,
                whitespace: true,
                max: 128,
                message: "请输入 1–128 个字符的序列号",
              },
            ]}
          >
            <Input autoComplete="off" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="lifecycleStatus"
            label="初始生命周期"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "DRAFT", label: "草稿" },
                { value: "ACTIVE", label: "启用" },
                { value: "MAINTENANCE", label: "维护中" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="connectivityState"
            label="连接状态"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "ONLINE", label: "在线" },
                { value: "OFFLINE", label: "离线" },
                { value: "DEGRADED", label: "降级" },
              ]}
            />
          </Form.Item>
          <Form.Item name="connectivitySource" label="连接事实来源">
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item name="connectivityReasonCode" label="状态原因代码">
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          {dialog === "create" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "新建机器人失败，请检查连接后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={createRobot.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createRobot.isPending}
            >
              创建机器人
            </Button>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={dialog === "edit"}
        title="编辑机器人"
        destroyOnHidden
        onCancel={() => {
          if (!updateRobot.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<EditRobotFormValues>
          form={editForm}
          layout="vertical"
          onFinish={submitEdit}
          requiredMark="optional"
        >
          <Form.Item
            name="displayName"
            label="显示名称"
            rules={[{ required: true, whitespace: true, max: 256 }]}
          >
            <Input autoFocus autoComplete="off" maxLength={256} />
          </Form.Item>
          <Form.Item
            name="connectivityState"
            label="连接状态"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "ONLINE", label: "在线" },
                { value: "OFFLINE", label: "离线" },
                { value: "DEGRADED", label: "降级" },
              ]}
            />
          </Form.Item>
          <Form.Item name="connectivitySource" label="连接事实来源">
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item name="connectivityReasonCode" label="状态原因代码">
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          {dialog === "edit" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "更新机器人失败，请重新加载后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={updateRobot.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={updateRobot.isPending}
            >
              保存更改
            </Button>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={dialog === "transition"}
        title="变更生命周期"
        destroyOnHidden
        onCancel={() => {
          if (!transitionRobot.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<TransitionRobotFormValues>
          form={transitionForm}
          layout="vertical"
          onFinish={submitTransition}
          requiredMark="optional"
        >
          <Form.Item
            name="lifecycleStatus"
            label="目标状态"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "ACTIVE", label: "启用" },
                { value: "MAINTENANCE", label: "维护中" },
                { value: "DISABLED", label: "已停用" },
                { value: "RETIRED", label: "已退役" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="reason"
            label="变更原因"
            rules={[{ required: true, whitespace: true, max: 512 }]}
          >
            <Input.TextArea
              autoFocus
              maxLength={512}
              autoSize={{ minRows: 3, maxRows: 6 }}
            />
          </Form.Item>
          {dialog === "transition" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "状态变更失败，请重新加载后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={transitionRobot.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={transitionRobot.isPending}
            >
              确认变更
            </Button>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={dialog === "maintenance"}
        title="新增维护记录"
        destroyOnHidden
        onCancel={() => {
          if (!createMaintenanceRecord.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<MaintenanceRecordFormValues>
          form={maintenanceForm}
          layout="vertical"
          onFinish={submitMaintenance}
          requiredMark="optional"
        >
          <Form.Item
            name="summary"
            label="维护摘要"
            rules={[{ required: true, whitespace: true, max: 256 }]}
          >
            <Input autoFocus autoComplete="off" maxLength={256} />
          </Form.Item>
          <Form.Item name="details" label="详细说明">
            <Input.TextArea
              maxLength={4000}
              autoSize={{ minRows: 4, maxRows: 10 }}
            />
          </Form.Item>
          {dialog === "maintenance" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "维护记录保存失败，请检查后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={createMaintenanceRecord.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createMaintenanceRecord.isPending}
            >
              保存记录
            </Button>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={dialog === "component-create"}
        title="添加组件"
        destroyOnHidden
        onCancel={() => {
          if (!createRobotComponent.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<CreateComponentFormValues>
          form={createComponentForm}
          layout="vertical"
          onFinish={submitCreateComponent}
          requiredMark="optional"
        >
          <Form.Item name="parentComponentId" label="父组件 ID（留空为根节点）">
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="componentModelId"
            label="组件模型 ID"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input autoFocus maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="componentType"
            label="组件类型"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input
              maxLength={128}
              autoComplete="off"
              placeholder="例如 CAMERA、ARM 或 GRIPPER"
            />
          </Form.Item>
          <Form.Item
            name="displayName"
            label="显示名称"
            rules={[{ required: true, whitespace: true, max: 256 }]}
          >
            <Input maxLength={256} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="serialNo"
            label="序列号"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="lifecycleStatus"
            label="初始生命周期"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "DRAFT", label: "草稿" },
                { value: "ACTIVE", label: "启用" },
                { value: "MAINTENANCE", label: "维护中" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="sortOrder"
            label="拓扑排序"
            rules={[{ required: true, type: "number", min: 0 }]}
          >
            <InputNumber
              min={0}
              max={2_147_483_647}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          {dialog === "component-create" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "添加组件失败，请重新加载拓扑后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={createRobotComponent.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createRobotComponent.isPending}
            >
              添加组件
            </Button>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={dialog === "component-edit"}
        title="编辑组件"
        destroyOnHidden
        onCancel={() => {
          if (!updateRobotComponent.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<EditComponentFormValues>
          form={editComponentForm}
          layout="vertical"
          onFinish={submitEditComponent}
          requiredMark="optional"
        >
          <Form.Item name="parentComponentId" label="父组件 ID（留空为根节点）">
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="componentModelId"
            label="组件模型 ID"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input autoFocus maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="componentType"
            label="组件类型"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="displayName"
            label="显示名称"
            rules={[{ required: true, whitespace: true, max: 256 }]}
          >
            <Input maxLength={256} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="serialNo"
            label="序列号"
            rules={[{ required: true, whitespace: true, max: 128 }]}
          >
            <Input maxLength={128} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="sortOrder"
            label="拓扑排序"
            rules={[{ required: true, type: "number", min: 0 }]}
          >
            <InputNumber
              min={0}
              max={2_147_483_647}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          {dialog === "component-edit" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "更新组件失败，请重新加载拓扑后重试。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={updateRobotComponent.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={updateRobotComponent.isPending}
            >
              保存组件
            </Button>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={dialog === "component-transition"}
        title="变更组件状态"
        destroyOnHidden
        onCancel={() => {
          if (!transitionRobotComponent.isPending) setDialog(null);
        }}
        footer={null}
      >
        <Form<TransitionComponentFormValues>
          form={transitionComponentForm}
          layout="vertical"
          onFinish={submitTransitionComponent}
          requiredMark="optional"
        >
          <Form.Item
            name="lifecycleStatus"
            label="目标状态"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "ACTIVE", label: "启用" },
                { value: "MAINTENANCE", label: "维护中" },
                { value: "DISABLED", label: "已停用" },
                { value: "RETIRED", label: "软移除（退役，保留历史引用）" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="reason"
            label="变更原因"
            rules={[{ required: true, whitespace: true, max: 512 }]}
          >
            <Input.TextArea
              autoFocus
              maxLength={512}
              autoSize={{ minRows: 3, maxRows: 6 }}
            />
          </Form.Item>
          {dialog === "component-transition" && mutationError ? (
            <Alert
              type="error"
              showIcon
              message={
                isDomainError(mutationError)
                  ? mutationError.message
                  : "组件状态变更失败；存在子组件时需先逐个退役。"
              }
            />
          ) : null}
          <Space>
            <Button
              onClick={() => setDialog(null)}
              disabled={transitionRobotComponent.isPending}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={transitionRobotComponent.isPending}
            >
              确认变更
            </Button>
          </Space>
        </Form>
      </Modal>
    </main>
  );
}

export default Component;
