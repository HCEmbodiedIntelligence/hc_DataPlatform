import { RobotDeletionDialog } from "../../../features/robots/RobotDeletionDialog";
import { RobotSchemeAssociations } from "../../../features/capture-schemes/RobotSchemeAssociations";
import { useScopedDeleteRobot } from "../../../features/robots/api/hooks";
import { ConnectionLink } from "../../../features/robot-ingest/ConnectionLink";
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
} from "antd";
import { Bot, Radio, Settings2, TriangleAlert, Plus } from "lucide-react";
import { FilterToolbar } from "../../../shared/ui";
import base from "../styles.module.css";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useIngestScope } from "../../../features/ingest/use-ingest-scope";
import {
  robotFilterDefaults,
  useRobotOnboarding,
  type OnboardingRobot,
  type RobotFilters,
} from "../../../features/robot-ingest/onboarding";
import { useOrganizationCapabilities } from "../../../shared/auth/use-capabilities";
import { parseRobotFilters, updateRobotFilters } from "../onboarding-query";
import { OnboardRobotDialog } from "./OnboardRobotDialog";
import { RobotIdentityConsole } from "./RobotIdentityConsole";
import { getRuntimeConfig } from "../../../shared/config/runtime";
import { robotConnectionBaseUrl } from "../../../features/robot-ingest/connection-url";
import styles from "../onboarding.module.css";

const connections = {
  ONLINE: "在线",
  OFFLINE: "离线",
  UNVERIFIED: "未验证",
  UNKNOWN: "未知",
};
const permissions = {
  NOT_ENABLED: "未开通",
  ENABLED: "已开通",
  DISABLED: "已停用",
};
const credentials = {
  NOT_ISSUED: "未签发",
  AVAILABLE: "有可用凭据",
  UNAVAILABLE: "无可用凭据",
};
const segments = {
  online: "在线机器人",
  pending: "待完成接入",
  attention: "需处理设备",
};
const options = (values: Record<string, string>) => [
  { value: "", label: "全部" },
  ...Object.entries(values).map(([value, label]) => ({ value, label })),
];
const when = (value: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "尚无有效通信";

function robotPlatformAddress() {
  try {
    return robotConnectionBaseUrl(getRuntimeConfig(), window.location.origin)
      .href;
  } catch {
    return "请配置机器人可访问的平台地址";
  }
}

export function RobotOnboardingTab() {
  const scope = useIngestScope();
  const capability = useOrganizationCapabilities(scope?.organizationId ?? null);
  const [params, setParams] = useSearchParams();
  const deleteRobot = useScopedDeleteRobot(scope?.organizationId ?? null);
  const [deleteTarget, setDeleteTarget] = useState<{
    organizationId: string;
    robot: OnboardingRobot;
  } | null>(null);
  const [deleteNotice, setDeleteNotice] = useState("");
  const closeDetail = () => {
    const next = new URLSearchParams(params);
    next.delete("robot.detail");
    next.delete("robot.section");
    setParams(next, { replace: true });
  };
  const openDelete = (robot: OnboardingRobot) => {
    if (!scope?.organizationId || !capability.has("robot.manage")) return;
    deleteRobot.reset();
    setDeleteTarget({ organizationId: scope.organizationId, robot });
  };
  const filters = useMemo(() => parseRobotFilters(params), [params]);
  const [query, setQuery] = useState(filters.q);
  const [wizard, setWizard] = useState<OnboardingRobot | "new" | null>(
    Boolean(params.get("robot.wizard")) ? "new" : null,
  );
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (wizard)
      next.set(
        "robot.wizard",
        wizard === "new"
          ? (params.get("robot.wizard") ?? "new")
          : wizard.robot_id,
      );
    else next.delete("robot.wizard");
    if (next.toString() !== params.toString())
      setParams(next, { replace: true });
  }, [wizard]);
  const page = useRobotOnboarding(
    scope,
    filters,
    capability.has("ingest_source.read"),
  );
  const selectedId = params.get("robot.detail");
  // A separate lookup over the complete organization avoids losing an open detail
  // when a live heartbeat moves its row out of the current filter or page.
  const detail = useRobotOnboarding(
    scope,
    { ...robotFilterDefaults, q: selectedId ?? "" },
    Boolean(selectedId) && capability.has("ingest_source.read"),
  );
  const selected = detail.data?.items.find(
    (item) => item.robot_id === selectedId,
  );
  const apply = (patch: Partial<RobotFilters>) =>
    setParams(updateRobotFilters(params, patch));
  const openDetail = (robot: OnboardingRobot) => {
    const next = new URLSearchParams(params);
    next.set("robot.detail", robot.robot_id);
    next.delete("robot.section");
    setParams(next);
  };
  const clear = () =>
    apply({
      ...robotFilterDefaults,
      sort: filters.sort,
      page_size: filters.page_size,
    });
  useEffect(() => setQuery(filters.q), [filters.q]);
  const summary = page.data?.summary;
  const count = (value: number | null | undefined) =>
    page.isError ? "暂不可用" : value === null ? "未知" : (value ?? "加载中");
  const cards = [
    {
      label: "机器人总数",
      eyebrow: "ROBOTS",
      detail: "当前组织的设备",
      icon: <Bot size={28} strokeWidth={1.65} />,
      value: summary?.total_count,
      segment: "",
    },
    {
      label: "在线机器人",
      eyebrow: "ONLINE",
      detail: "5 分钟内有效通信",
      icon: <Radio size={28} strokeWidth={1.65} />,
      value: summary?.online_count,
      segment: "online",
    },
    {
      label: "待完成接入",
      eyebrow: "ONBOARDING",
      detail: "待配置或验证连接",
      icon: <Settings2 size={28} strokeWidth={1.65} />,
      value: summary?.pending_count,
      segment: "pending",
    },
    {
      label: "需处理设备",
      eyebrow: "ISSUES",
      detail: "尚未恢复的接入故障",
      icon: <TriangleAlert size={28} strokeWidth={1.65} />,
      value: summary?.attention_count,
      segment: "attention",
    },
  ];
  if (!scope) return <Empty description="请选择组织后查看机器人接入" />;
  if (!capability.loading && !capability.has("ingest_source.read"))
    return <Alert type="error" title="没有当前组织的机器人接入读取权限" />;
  return (
    <section className={styles.panel} aria-label="机器人接入">
      {deleteNotice && (
        <Alert
          type="success"
          showIcon
          title={deleteNotice}
          closable
          onClose={() => setDeleteNotice("")}
        />
      )}
      <Flex align="center" justify="space-between" wrap gap={12}>
        <div>
          <p>
            当前组织：{scope.organizationId} · 每台设备一个 Robot
            ID，上传归属由采集任务决定
          </p>
        </div>
        {capability.has("ingest_source.manage") && (
          <Button
            type="primary"
            icon={<Plus size={16} />}
            onClick={() => setWizard("new")}
          >
            接入机器人
          </Button>
        )}
      </Flex>
      <div className={`${base.sourceMetricStrip} ${styles.summarySurface}`}>
        {cards.map((card) => (
          <button
            className={`${base.sourceMetric} ${styles.metricAction} ${card.segment === "attention" ? base.sourceMetricWarning : card.segment === "online" ? base.sourceMetricSuccess : ""}`}
            key={card.label}
            aria-label={card.label}
            aria-pressed={
              filters.segment === card.segment &&
              Boolean(
                card.segment ||
                  (!filters.q &&
                    !filters.connection &&
                    !filters.permission &&
                    !filters.credential),
              )
            }
            onClick={() =>
              apply({
                ...robotFilterDefaults,
                sort: filters.sort,
                page_size: filters.page_size,
                segment: card.segment,
              })
            }
          >
            <span className={base.sourceMetricIcon} aria-hidden="true">
              {card.icon}
            </span>
            <span className={base.sourceMetricCopy}>
              <span className={base.sourceMetricEyebrow}>{card.eyebrow}</span>
              <span className={styles.metricLabel}>{card.label}</span>
              <strong>{count(card.value)}</strong>
              <span className={base.sourceMetricDetail}>{card.detail}</span>
            </span>
          </button>
        ))}
      </div>
      <p className={styles.hint}>
        在线依据 5
        分钟内的有效设备认证或心跳。正常空闲离线、主动停用和首次配置未完成不计为故障。
        {summary?.unknown_count
          ? ` ${summary.unknown_count} 台设备的通信观测不足。`
          : ""}
      </p>
      <div className={styles.filters}>
        <FilterToolbar
          label="机器人筛选"
          onApply={() => apply({ q: query })}
          onReset={clear}
        >
          <label className={styles.search}>
            搜索
            <Input.Search
              aria-label="搜索机器人"
              placeholder="名称、序列号、Robot ID"
              value={query}
              allowClear
              onChange={(e) => {
                setQuery(e.target.value);
                if (!e.target.value) apply({ q: "" });
              }}
              onSearch={(q) => apply({ q })}
            />
          </label>
          <label>
            连接状态
            <Select
              aria-label="连接状态"
              value={filters.connection}
              options={options(connections)}
              onChange={(connection) => apply({ connection })}
            />
          </label>
          <label>
            上传权限
            <Select
              aria-label="上传权限"
              value={filters.permission}
              options={options(permissions)}
              onChange={(permission) => apply({ permission })}
            />
          </label>
        </FilterToolbar>
      </div>
      <Collapse
        ghost
        items={[
          {
            key: "more",
            label: "更多筛选",
            children: (
              <label className={styles.moreFilter}>
                凭据状态
                <Select
                  aria-label="凭据状态"
                  value={filters.credential}
                  options={options(credentials)}
                  onChange={(credential) => apply({ credential })}
                />
              </label>
            ),
          },
        ]}
      />
      <Flex gap={8} wrap align="center" aria-label="已生效筛选">
        {(["segment", "q", "connection", "permission", "credential"] as const)
          .filter((key) => filters[key])
          .map((key) => {
            const label =
              key === "q"
                ? `搜索：${filters.q}`
                : (
                    {
                      ...segments,
                      ...connections,
                      ...permissions,
                      ...credentials,
                    } as Record<string, string>
                  )[filters[key]];
            return (
              <Tag key={key} closable onClose={() => apply({ [key]: "" })}>
                {label}
              </Tag>
            );
          })}
        {Object.entries(filters).some(
          ([key, value]) =>
            ["q", "connection", "permission", "credential", "segment"].includes(
              key,
            ) && value,
        ) && (
          <Button type="link" onClick={clear}>
            清除筛选
          </Button>
        )}
      </Flex>
      <Flex align="center" justify="space-between" wrap gap={12}>
        <span>
          {page.isError
            ? "匹配数量暂不可用"
            : `匹配 ${page.data?.total ?? "…"} 台机器人`}
        </span>
        <Space>
          <Button onClick={() => void page.refetch()}>刷新</Button>
          <label>
            排序{" "}
            <Select
              aria-label="机器人排序"
              value={filters.sort}
              onChange={(sort) => apply({ sort })}
              options={[
                { value: "updated", label: "最近更新" },
                { value: "name", label: "名称" },
                { value: "communication", label: "最近设备通信" },
              ]}
            />
          </label>
        </Space>
      </Flex>
      {page.isError && (
        <Alert
          type="error"
          showIcon
          title="机器人接入暂不可用"
          description={page.error.message}
          action={<Button onClick={() => void page.refetch()}>重试</Button>}
        />
      )}
      <Table<OnboardingRobot>
        className="hc-data-table"
        rowKey="robot_id"
        dataSource={page.isError ? [] : page.data?.items}
        loading={page.isPending && !page.isError}
        scroll={{ x: 900 }}
        locale={{
          emptyText: page.isError
            ? "暂不可用"
            : "没有匹配的机器人，可清除筛选或接入设备",
        }}
        pagination={{
          current: filters.page,
          pageSize: filters.page_size,
          total: page.data?.total,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          onChange: (pageNumber, size) =>
            apply(
              size !== filters.page_size
                ? { page_size: size }
                : { page: pageNumber },
            ),
        }}
        columns={[
          {
            title: "机器人",
            key: "robot",
            render: (_, item) => (
              <div>
                <strong>{item.display_name}</strong>
                <div>{item.serial_no}</div>
                <code>{item.robot_id}</code>
              </div>
            ),
          },
          {
            title: "上传权限",
            key: "permission",
            render: (_, item) => permissions[item.upload_permission],
          },
          {
            title: "凭据状态",
            key: "credential",
            render: (_, item) => credentials[item.credential_availability],
          },
          {
            title: "连接状态",
            key: "connection",
            render: (_, item) => (
              <>
                <Tag
                  color={
                    item.connection_state === "ONLINE" ? "green" : "default"
                  }
                >
                  {connections[item.connection_state]}
                </Tag>
                <div>{when(item.last_communication_at)}</div>
              </>
            ),
          },
          {
            title: "待处理原因",
            key: "faults",
            render: (_, item) =>
              item.faults.length
                ? item.faults.map((f) => <div key={f.code}>{f.reason}</div>)
                : "—",
          },
          {
            title: "操作",
            key: "action",
            render: (_, item) => (
              <Space>
                <Button type="link" onClick={() => openDetail(item)}>
                  {item.identity ? "查看接入详情" : "继续配置"}
                </Button>
                {capability.has("robot.manage") && (
                  <Button type="link" danger onClick={() => openDelete(item)}>
                    删除机器人
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Drawer
        open={Boolean(selectedId)}
        title={selected?.display_name ?? "机器人接入详情"}
        width="min(1100px, 96vw)"
        destroyOnHidden
        onClose={closeDetail}
        extra={
          selected && capability.has("robot.manage") ? (
            <Button danger onClick={() => openDelete(selected)}>
              删除机器人
            </Button>
          ) : null
        }
      >
        {detail.isError ? (
          <Alert
            type="error"
            title="设备详情暂不可用"
            action={<Button onClick={() => void detail.refetch()}>重试</Button>}
          />
        ) : selected ? (
          <>
            <Tabs
              activeKey={
                ["overview", "connection", "processing"].includes(
                  params.get("robot.section") ?? "",
                )
                  ? params.get("robot.section")!
                  : "overview"
              }
              onChange={(key) => {
                const next = new URLSearchParams(params);
                next.set("robot.section", key);
                setParams(next);
              }}
              destroyOnHidden
              items={[
                {
                  key: "overview",
                  label: "设备概览",
                  children: (
                    <Space
                      orientation="vertical"
                      size="large"
                      style={{ width: "100%" }}
                    >
                      <Descriptions
                        column={2}
                        items={[
                          {
                            key: "id",
                            label: "Robot ID",
                            children: selected.robot_id,
                          },
                          {
                            key: "serial",
                            label: "序列号",
                            children: selected.serial_no,
                          },
                          {
                            key: "model",
                            label: "模型",
                            children: selected.model_version_id ?? "未绑定",
                          },
                          {
                            key: "connection",
                            label: "连接验证",
                            children: connections[selected.connection_state],
                          },
                          {
                            key: "communication",
                            label: "最近设备通信",
                            children: when(selected.last_communication_at),
                          },
                          {
                            key: "endpoint",
                            label: "平台地址",
                            children: robotPlatformAddress(),
                          },
                        ]}
                      />
                      {selected.legacy_sources.length > 0 && (
                        <Collapse
                          items={[
                            {
                              key: "legacy",
                              label: `原项目关联（${selected.legacy_sources.length}）`,
                              children: (
                                <>
                                  <p>
                                    旧数据源和历史上传已保留。人工导入继续遵守对应项目和
                                    Region 的停用配置；机器人上传权限独立管理。
                                  </p>
                                  {selected.legacy_sources.map((source) => (
                                    <p
                                      key={`${source.project_id}/${source.region_code}/${source.source_id}`}
                                    >
                                      {source.project_id} / {source.region_code}{" "}
                                      · {source.source_id} ·{" "}
                                      {source.administrative_state ===
                                      "DISABLED"
                                        ? "已停用"
                                        : "已启用"}
                                    </p>
                                  ))}
                                  {new Set(
                                    selected.legacy_sources.map(
                                      (source) => source.administrative_state,
                                    ),
                                  ).size > 1 && (
                                    <Alert
                                      type="warning"
                                      title="旧项目配置存在不同启用状态"
                                      description="同一项目和 Region 中有停用记录时，人工导入保持禁止；请核对原项目配置。接入流程不会自动改写这些状态。"
                                    />
                                  )}
                                </>
                              ),
                            },
                          ]}
                        />
                      )}
                      {selected.faults.map((fault) => (
                        <Alert
                          key={fault.code}
                          type="warning"
                          title={fault.reason}
                          description={`${when(fault.occurred_at)} · ${fault.action}`}
                        />
                      ))}
                      <Space>
                        <Tag>{permissions[selected.upload_permission]}</Tag>
                        <Tag>
                          {credentials[selected.credential_availability]}
                        </Tag>
                      </Space>
                      <Button
                        type="primary"
                        onClick={() => {
                          const next = new URLSearchParams(params);
                          next.set("robot.section", "connection");
                          setParams(next);
                        }}
                      >
                        管理上传与连接
                      </Button>
                    </Space>
                  ),
                },
                {
                  key: "connection",
                  label: "上传与连接",
                  children: (
                    <Space
                      orientation="vertical"
                      size="large"
                      style={{ width: "100%" }}
                    >
                      {selected.identity &&
                        capability.has("ingest_source.manage") && (
                          <ConnectionLink
                            scope={scope}
                            identityId={selected.identity.ingest_identity_id}
                            disabled={selected.upload_permission !== "ENABLED"}
                          />
                        )}
                      {selected.identity ? (
                        <RobotIdentityConsole
                          scope={scope}
                          canRead
                          canManage={capability.has("ingest_source.manage")}
                          identityId={selected.identity.ingest_identity_id}
                        />
                      ) : (
                        <Empty description="设备已登记，尚未开通上传">
                          {capability.has("ingest_source.manage") && (
                            <Button
                              type="primary"
                              onClick={() => setWizard(selected)}
                            >
                              继续配置
                            </Button>
                          )}
                        </Empty>
                      )}
                    </Space>
                  ),
                },
                {
                  key: "processing",
                  label: "采集方案",
                  children: (
                    <>
                      <RobotSchemeAssociations
                        organizationId={scope.organizationId!}
                        robotId={selected.robot_id}
                        canManage={capability.has("robot.manage")}
                        modelVersionId={selected.model_version_id}
                      />
                    </>
                  ),
                },
              ]}
            />
          </>
        ) : (
          <Empty
            description={
              detail.isPending ? "正在读取设备" : "设备不存在或已删除"
            }
          />
        )}
      </Drawer>
      {deleteTarget && deleteTarget.organizationId === scope.organizationId && (
        <RobotDeletionDialog
          key={deleteTarget.organizationId + deleteTarget.robot.robot_id}
          organizationId={deleteTarget.organizationId}
          robot={{
            id: deleteTarget.robot.robot_id,
            displayName: deleteTarget.robot.display_name,
            serialNo: deleteTarget.robot.serial_no,
          }}
          canManage={capability.has("robot.manage")}
          pending={deleteRobot.isPending}
          error={deleteRobot.error}
          onCancel={() => setDeleteTarget(null)}
          onDelete={() =>
            deleteRobot.mutate(deleteTarget.robot.robot_id, {
              onSuccess: () => {
                setDeleteNotice(
                  `机器人“${deleteTarget.robot.display_name}”已删除。`,
                );
                if (selectedId === deleteTarget.robot.robot_id) closeDetail();
                setDeleteTarget(null);
                void page.refetch();
              },
            })
          }
        />
      )}
      {wizard && (
        <OnboardRobotDialog
          key={`${scope.organizationId}:${scope.projectId}:${scope.regionCode}`}
          scope={scope}
          resumeRobotId={params.get("robot.wizard") ?? undefined}
          robot={wizard === "new" ? undefined : wizard}
          canCreate={
            capability.has("robot.manage") &&
            capability.has("robot_model.manage")
          }
          onClose={() => {
            setWizard(null);
            void page.refetch();
          }}
          onDone={(item) => {
            setWizard(null);
            openDetail(item);
            void page.refetch();
            void detail.refetch();
          }}
        />
      )}
    </section>
  );
}
