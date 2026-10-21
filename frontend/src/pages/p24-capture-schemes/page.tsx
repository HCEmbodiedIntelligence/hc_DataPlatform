import {
  Alert,
  Button,
  Checkbox,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useCurrentOrganizationId } from "../../shared/scope/current-organization";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { StandardPageScaffold } from "../../shared/ui";
import { useScopedRobotModels } from "../../features/robot-models/api/hooks";
import { useScopedRobots } from "../../features/robots/api/hooks";
import {
  captureApi,
  useCaptureMutation,
  useCaptureQuery,
} from "../../features/capture-schemes/api";
import {
  documentSchema,
  type SchemeDetail,
  type SchemeSummary,
} from "../../features/capture-schemes/contracts";
import { SchemeEditor } from "../../features/capture-schemes/SchemeEditor";
import { DefinitionFields } from "../../features/capture-schemes/DefinitionFields";
import {
  Differences,
  EvidenceView,
} from "../../features/capture-schemes/EvidenceView";
import {
  captureError,
  dataSummary,
  structureDiff,
} from "../../features/capture-schemes/presentation";
import {
  safeReturnTo,
  schemeLibraryPath,
} from "../../features/capture-schemes/navigation";
import { createMutationIntentKey } from "../../features/ingest/mutation-machine";
import styles from "./styles.module.css";

export function Component() {
  const org = useCurrentOrganizationId();
  return org ? (
    <Library key={org} org={org} />
  ) : (
    <Alert type="info" title="请先选择组织" />
  );
}
function Library({ org }: { org: string }) {
  const capability = useOrganizationCapabilities(org);
  const manage = capability.has("robot.manage"),
    read = capability.has("robot.read");
  const location = useLocation(),
    navigate = useNavigate();
  const { schemeId } = useParams();
  const [params, setParams] = useSearchParams();
  const back = safeReturnTo(params.get("returnTo")) ?? undefined;
  const create = location.pathname.endsWith("/new");
  const newId = params.get("draft") ?? "pending";
  useEffect(() => {
    if (create && !params.has("draft")) {
      const next = new URLSearchParams(params);
      next.set("draft", `scheme-${createMutationIntentKey()}`);
      setParams(next, { replace: true });
    }
  }, [create, params, setParams]);
  const [archive, setArchive] = useState<SchemeSummary | SchemeDetail | null>(
    null,
  );
  const filter = {
    q: params.get("q") ?? "",
    source_format: params.get("format") || undefined,
    include_archived: params.get("archived") === "1",
  };
  const list = useCaptureQuery(
    org,
    "list",
    filter,
    (signal) => captureApi.list(org, filter, signal),
    read && !schemeId && !create,
  );
  const sourceId = schemeId ?? params.get("copy");
  const detail = useCaptureQuery(
    org,
    "detail",
    sourceId,
    (signal) => captureApi.detail(org, sourceId!, signal),
    read && Boolean(sourceId),
  );
  const references = useCaptureQuery(
    org,
    "references",
    schemeId,
    (signal) => captureApi.references(org, schemeId!, signal),
    read && Boolean(schemeId),
  );
  const models = useScopedRobotModels(read ? org : null),
    robots = useScopedRobots(read ? org : null);
  const modelNames = new Map(
    (models.data?.items ?? []).map((m) => [
      m.currentPublishedVersionId,
      m.displayName,
    ]),
  );
  const robotNames = new Map(
    (robots.data?.items ?? []).map((r) => [r.id, r.displayName]),
  );
  const archiveAction = useCaptureMutation(
    org,
    async (item: SchemeSummary | SchemeDetail) =>
      captureApi.archive(org, item.scheme_id, item.revision),
  );
  const route = (id?: string, isNew = false) =>
    schemeLibraryPath(back, id, isNew);
  const edit = (id: string) =>
    navigate(`${route(id)}${back ? "&" : "?"}edit=1`);
  const copy = (id: string, version?: number) =>
    navigate(
      `${route(undefined, true)}${back ? "&" : "?"}copy=${encodeURIComponent(id)}${version ? `&copyVersion=${version}` : ""}`,
    );
  const current = detail.data;
  const selectedVersion =
    current?.versions.find(
      (v) => v.version === Number(params.get("version")),
    ) ?? current?.versions[0];
  const previousVersion =
    selectedVersion &&
    current?.versions.find(
      (version) => version.version < selectedVersion.version,
    );
  const copiedVersion =
    current?.versions.find(
      (version) => version.version === Number(params.get("copyVersion")),
    ) ?? current?.versions[0];
  const isEditor =
    create || (manage && params.get("edit") === "1" && !current?.archived);
  const errors = [
    !create && !schemeId ? list.error : null,
    sourceId ? detail.error : null,
    schemeId ? references.error : null,
    archiveAction.error,
  ].filter(Boolean);
  const listBody = (
    <>
      <div className={styles.toolbar}>
        <Space wrap>
          <Input
            aria-label="搜索采集方案"
            placeholder="搜索名称或说明"
            value={filter.q}
            onChange={(e) => {
              const next = new URLSearchParams(params);
              next.set("q", e.target.value);
              setParams(next, { replace: true });
            }}
          />
          <Select
            aria-label="输入格式筛选"
            style={{ width: 160 }}
            allowClear
            placeholder="全部格式"
            value={filter.source_format}
            options={[
              { value: "MCAP", label: "MCAP" },
              { value: "LEROBOT_V3", label: "LeRobot v3" },
            ]}
            onChange={(value) => {
              const next = new URLSearchParams(params);
              if (value) next.set("format", value);
              else next.delete("format");
              setParams(next);
            }}
          />
          <Checkbox
            checked={filter.include_archived}
            onChange={(e) => {
              const next = new URLSearchParams(params);
              next.set("archived", e.target.checked ? "1" : "0");
              setParams(next);
            }}
          >
            包含已归档
          </Checkbox>
        </Space>
      </div>
      <Table
        rowKey="scheme_id"
        loading={list.isFetching}
        scroll={{ x: 1150 }}
        dataSource={list.data ?? []}
        locale={{ emptyText: list.error ? "方案读取失败" : "暂无采集方案" }}
        columns={[
          {
            title: "名称",
            dataIndex: "draft",
            render: (_, item) => (
              <Link to={route(item.scheme_id)}>{item.draft.name}</Link>
            ),
          },
          {
            title: "适用范围",
            render: (_, item) =>
              item.draft.model_version_ids
                .map((id) => modelNames.get(id) ?? id)
                .join("、") || "不限型号，验证数据能力",
          },
          {
            title: "格式",
            render: (_, item) =>
              item.draft.adapters
                .map((a) =>
                  a.source_format === "LEROBOT_V3" ? "LeRobot v3" : "MCAP",
                )
                .join(" / "),
          },
          { title: "数据摘要", render: (_, item) => dataSummary(item.draft) },
          {
            title: "最新版本",
            render: (_, item) =>
              item.latest_version ? `v${item.latest_version}` : "未发布",
          },
          { title: "实例数", dataIndex: "instance_count" },
          { title: "任务数", dataIndex: "task_count" },
          {
            title: "状态",
            render: (_, item) => (
              <Tag>
                {item.archived
                  ? "已归档"
                  : item.latest_version
                    ? "已发布"
                    : "待发布"}
              </Tag>
            ),
          },
          {
            title: "操作",
            render: (_, item) => (
              <Space wrap>
                <Link to={route(item.scheme_id)}>查看引用</Link>
                {manage && (
                  <>
                    <Button
                      size="small"
                      disabled={item.archived}
                      onClick={() => edit(item.scheme_id)}
                    >
                      编辑方案
                    </Button>
                    <Button
                      size="small"
                      disabled={!item.latest_version}
                      onClick={() => copy(item.scheme_id)}
                    >
                      复制新方案
                    </Button>
                    <Button
                      size="small"
                      disabled={item.archived}
                      onClick={() => setArchive(item)}
                    >
                      归档
                    </Button>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />
    </>
  );
  return (
    <div className={styles.page} data-page-id="P24">
      <StandardPageScaffold
        header={{
          title: create ? "新建方案" : isEditor ? "编辑方案" : "采集方案",
          breadcrumbs: [
            { key: "governance", label: "治理与资产" },
            { key: "schemes", label: "采集方案", to: route() },
            ...(create || schemeId
              ? [
                  {
                    key: "current",
                    label: create
                      ? "新建方案"
                      : isEditor
                        ? "编辑方案"
                        : "方案详情",
                  },
                ]
              : []),
          ],
          actions: (
            <Space wrap>
              {(create || schemeId) && (
                <Button
                  icon={<ArrowLeft size={16} />}
                  onClick={() => navigate(route())}
                >
                  返回方案列表
                </Button>
              )}
              {back && <Link to={back}>返回原表单</Link>}
              {manage && !isEditor ? (
                <Button
                  type="primary"
                  onClick={() => navigate(route(undefined, true))}
                >
                  新建方案
                </Button>
              ) : !manage ? (
                <Tag>只读</Tag>
              ) : null}
            </Space>
          ),
        }}
      >
        <div className={styles.content}>
          {!read ? (
            <Alert type="warning" title="当前组织没有采集方案读取权限" />
          ) : (
            <>
              {errors.map((error, i) => (
                <Alert
                  key={i}
                  type="error"
                  title={captureError(error)}
                  action={
                    <Button
                      onClick={() => {
                        void list.refetch();
                        if (sourceId) void detail.refetch();
                      }}
                    >
                      重新加载
                    </Button>
                  }
                />
              ))}
              {!schemeId && !create ? (
                listBody
              ) : (
                <>
                  <Space>
                    {current && (
                      <Tag>
                        {current.archived
                          ? "已归档"
                          : `方案修订 ${current.revision}`}
                      </Tag>
                    )}
                  </Space>
                  {detail.isFetching && <p>正在读取方案…</p>}
                  {isEditor &&
                  manage &&
                  (!create || newId !== "pending") &&
                  (!sourceId || current) ? (
                    <SchemeEditor
                      key={`${org}:${schemeId ?? newId}`}
                      organizationId={org}
                      schemeId={schemeId ?? newId}
                      detail={schemeId ? current : undefined}
                      initial={
                        !schemeId && copiedVersion
                          ? {
                              ...documentSchema.parse(copiedVersion),
                              name: `${copiedVersion.name} 副本`,
                            }
                          : undefined
                      }
                      back={back}
                      onSaved={() => {
                        void detail.refetch();
                      }}
                    />
                  ) : create ? (
                    !manage && (
                      <Alert
                        type="warning"
                        title="创建方案需要组织机器人管理权限"
                      />
                    )
                  ) : (
                    current && (
                      <>
                        <h2>{selectedVersion?.name ?? current.draft.name}</h2>
                        <p>{dataSummary(selectedVersion ?? current.draft)}</p>
                        <p>
                          {selectedVersion?.description ??
                            current.draft.description}
                        </p>
                        {selectedVersion && (
                          <>
                            <Tag color="green">
                              已发布 v{selectedVersion.version} · 只读
                            </Tag>
                            <Select
                              aria-label="查看发布版本"
                              value={selectedVersion.version}
                              options={current.versions.map((v) => ({
                                value: v.version,
                                label: `v${v.version}`,
                              }))}
                              onChange={(v) => {
                                const next = new URLSearchParams(params);
                                next.set("version", String(v));
                                setParams(next);
                              }}
                            />
                          </>
                        )}
                        {manage && (
                          <Space>
                            <Button
                              disabled={current.archived}
                              onClick={() => edit(current.scheme_id)}
                            >
                              编辑方案
                            </Button>
                            <Button
                              disabled={!selectedVersion}
                              onClick={() =>
                                copy(
                                  current.scheme_id,
                                  selectedVersion?.version,
                                )
                              }
                            >
                              复制新方案
                            </Button>
                            <Button
                              disabled={current.archived}
                              onClick={() => setArchive(current)}
                            >
                              归档方案
                            </Button>
                          </Space>
                        )}
                        <DefinitionFields
                          document={selectedVersion ?? current.draft}
                          disabled
                          onChange={() => {}}
                        />
                        <details>
                          <summary>完整配置与输入适配（JSON）</summary>
                          <pre>
                            {JSON.stringify(
                              documentSchema.parse(
                                selectedVersion ?? current.draft,
                              ),
                              null,
                              2,
                            )}
                          </pre>
                        </details>
                        {selectedVersion && (
                          <EvidenceView
                            items={Object.values(selectedVersion.validations)}
                          />
                        )}
                        {previousVersion && selectedVersion && (
                          <details>
                            <summary>与上一发布版本比较</summary>
                            <Differences
                              items={structureDiff(
                                documentSchema.parse(previousVersion),
                                documentSchema.parse(selectedVersion),
                              )}
                            />
                          </details>
                        )}
                        <h3>实例引用与可升级实例</h3>
                        <Table
                          size="small"
                          rowKey="robot_id"
                          pagination={false}
                          dataSource={references.data?.instances ?? []}
                          columns={[
                            {
                              title: "实例",
                              render: (_, row) => (
                                <Link
                                  to={`/settings/robot-instances?robot=${encodeURIComponent(row.robot_id)}&section=processing&returnTo=${encodeURIComponent(location.pathname + location.search)}`}
                                >
                                  {robotNames.get(row.robot_id) ?? row.robot_id}
                                </Link>
                              ),
                            },
                            {
                              title: "关联版本",
                              render: (_, r) =>
                                `v${r.version} / 映射 ${r.mapping_version}`,
                            },
                            {
                              title: "状态",
                              render: (_, r) =>
                                !r.active
                                  ? "已移除"
                                  : current.archived
                                    ? "方案已归档"
                                    : r.version < (current.latest_version ?? 0)
                                      ? `可升级至 v${current.latest_version}（需验证映射）`
                                      : "当前版本",
                            },
                          ]}
                        />
                        <h3>任务引用</h3>
                        <p>发布新版不会改变这些任务固定的方案和映射。</p>
                        <Table
                          size="small"
                          pagination={false}
                          rowKey={(r) =>
                            `${r.project_id}:${r.collection_task_id}:${r.revision}`
                          }
                          dataSource={references.data?.tasks ?? []}
                          columns={[
                            {
                              title: "项目 / 任务",
                              render: (_, r) =>
                                `${r.project_id} / ${r.collection_task_id}`,
                            },
                            {
                              title: "方案版本",
                              render: (_, r) => `v${r.version}`,
                            },
                            { title: "计划修订", dataIndex: "revision" },
                            {
                              title: "状态",
                              render: (_, r) =>
                                r.locked_at ? "已固定" : "尚未开始",
                            },
                          ]}
                        />
                      </>
                    )
                  )}
                </>
              )}
            </>
          )}
        </div>
      </StandardPageScaffold>
      <Modal
        open={Boolean(archive)}
        title="归档采集方案？"
        okText="归档"
        cancelText="取消"
        confirmLoading={archiveAction.isPending}
        onCancel={() => setArchive(null)}
        onOk={() =>
          archive &&
          archiveAction.mutate(archive, { onSuccess: () => setArchive(null) })
        }
      >
        <p>归档后不能用于新关联或新任务。发布版本与历史任务快照保留。</p>
      </Modal>
    </div>
  );
}
