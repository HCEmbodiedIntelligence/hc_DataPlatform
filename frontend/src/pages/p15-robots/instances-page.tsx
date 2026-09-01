import { useQueries } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Descriptions,
  Flex,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import {
  Bot,
  Boxes,
  Cable,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { Robot, RobotLifecycle } from "../../entities/robot";
import {
  useBindRobotModelVersion,
  useRobotModels,
} from "../../features/robot-models/api";
import {
  getRobotBootstrap,
  useCreateRobot,
  useDeleteProvisionalRobot,
  useRobotBootstrap,
  useRobots,
  useTransitionRobotLifecycle,
  useUpdateRobot,
} from "../../features/robots/api";
import { createMutationIntentKey } from "../../features/ingest/mutation-machine";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  EntityDrawer,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from "../../shared/ui";
import styles from "./instances-styles.module.css";

const lifecycleLabels: Readonly<Record<RobotLifecycle, string>> = {
  DRAFT: "草稿",
  ACTIVE: "已启用",
  MAINTENANCE: "维护中",
  DISABLED: "已停用",
  RETIRED: "已退役",
  UNKNOWN: "未知",
};

const lifecycleTransitions: Readonly<
  Record<
    RobotLifecycle,
    readonly Exclude<RobotLifecycle, "DRAFT" | "UNKNOWN">[]
  >
> = {
  DRAFT: ["ACTIVE", "RETIRED"],
  ACTIVE: ["MAINTENANCE", "DISABLED", "RETIRED"],
  MAINTENANCE: ["ACTIVE", "DISABLED", "RETIRED"],
  DISABLED: ["ACTIVE", "RETIRED"],
  RETIRED: [],
  UNKNOWN: [],
};

function operationError(error: unknown): string | null {
  if (!error) return null;
  if (isDomainError(error)) {
    const message =
      error.problemCode === "ROBOT_SERIAL_CONFLICT"
        ? "该序列号已对应另一台机器人，请查看并关联已有实例。"
        : error.message;
    return error.requestId ? `${message} 请求 ID：${error.requestId}` : message;
  }
  return error instanceof Error ? error.message : "操作未完成，请稍后重试。";
}

function modelLabel(
  versionId: string | null,
  versions: ReadonlyMap<string, string>,
): string {
  if (!versionId) return "未绑定";
  return versions.get(versionId) ?? versionId;
}

export function Component() {
  const capabilities = useCapabilities();
  const canRead = capabilities.has("robot.read");
  const canManage = capabilities.has("robot.manage");
  const canBindModel = capabilities.has("robot_model.manage");
  const [query, setQuery] = useState("");
  const [lifecycle, setLifecycle] = useState("");
  const robots = useRobots({
    ...(query.trim() ? { q: query.trim() } : {}),
    ...(lifecycle ? { lifecycle_status: lifecycle } : {}),
  });
  const models = useRobotModels();
  const items = robots.data?.items ?? [];
  const bootstraps = useQueries({
    queries: items.map((robot) => ({
      queryKey: ["robots", "organization-bootstrap", robot.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        getRobotBootstrap(robot.id, signal),
      staleTime: 15_000,
    })),
  });
  const bootstrapById = new Map(
    bootstraps.flatMap((result, index) =>
      result.data && items[index]
        ? [[items[index]!.id, result.data] as const]
        : [],
    ),
  );
  const versionLabels = useMemo(
    () =>
      new Map(
        (models.data?.items ?? []).flatMap((model) =>
          model.currentPublishedVersionId
            ? [
                [
                  model.currentPublishedVersionId,
                  `${model.displayName} · ${model.manufacturer}/${model.modelCode}`,
                ] as const,
              ]
            : [],
        ),
      ),
    [models.data?.items],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const detail = useRobotBootstrap(selectedId);
  const selected = detail.data ?? null;
  const createRobot = useCreateRobot();
  const bindModel = useBindRobotModelVersion();
  const updateRobot = useUpdateRobot();
  const transitionRobot = useTransitionRobotLifecycle();
  const deleteRobot = useDeleteProvisionalRobot();

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSerial, setCreateSerial] = useState("");
  const [createModelVersion, setCreateModelVersion] = useState("");
  const [createAttempt, setCreateAttempt] = useState<Robot | null>(null);
  const [createFlowError, setCreateFlowError] = useState<string | null>(null);
  const existingSerial = items.find(
    (robot) => robot.serialNo === createSerial.trim(),
  );
  const createPending = createRobot.isPending || bindModel.isPending;

  const resetCreate = () => {
    setCreateOpen(false);
    setCreateName("");
    setCreateSerial("");
    setCreateModelVersion("");
    setCreateAttempt(null);
    setCreateFlowError(null);
    createRobot.reset();
    bindModel.reset();
  };

  const closeCreate = () => {
    if (createPending) return;
    resetCreate();
  };

  const submitCreate = async () => {
    if (existingSerial) {
      closeCreate();
      setSelectedId(existingSerial.id);
      return;
    }
    if (!createName.trim() || !createSerial.trim() || !createModelVersion)
      return;
    setCreateFlowError(null);
    try {
      const robot =
        createAttempt ??
        (await createRobot.mutateAsync({
          displayName: createName.trim(),
          serialNo: createSerial.trim(),
          lifecycleStatus: "ACTIVE",
          connectivityState: "OFFLINE",
          connectivitySource: "robot-instance-console",
          connectivityReasonCode: "AWAITING_DEVICE_BINDING",
          idempotencyKey: createMutationIntentKey(),
        }));
      setCreateAttempt(robot);
      if (
        robot.effectiveModelBinding?.robotModelVersionId !== createModelVersion
      ) {
        await bindModel.mutateAsync({
          versionId: createModelVersion,
          robotId: robot.id,
          robotEtag: robot.etag,
          idempotencyKey: createMutationIntentKey(),
        });
      }
      resetCreate();
      setSelectedId(robot.id);
    } catch (error) {
      setCreateFlowError(operationError(error));
    }
  };

  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [lifecycleOpen, setLifecycleOpen] = useState(false);
  const [lifecycleTarget, setLifecycleTarget] = useState<
    Exclude<RobotLifecycle, "DRAFT" | "UNKNOWN"> | ""
  >("");
  const [lifecycleReason, setLifecycleReason] = useState("");
  const [bindOpen, setBindOpen] = useState(false);
  const [bindVersion, setBindVersion] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);

  const publishedModelOptions = (models.data?.items ?? []).flatMap((model) =>
    model.currentPublishedVersionId
      ? [
          {
            value: model.currentPublishedVersionId,
            label: `${model.displayName} · ${model.manufacturer}/${model.modelCode}`,
          },
        ]
      : [],
  );

  const table = (
    <Table
      rowKey="id"
      pagination={false}
      dataSource={items}
      scroll={{ x: 900 }}
      columns={[
        {
          title: "机器人实例",
          key: "robot",
          render: (_value, robot) => (
            <Button type="link" onClick={() => setSelectedId(robot.id)}>
              {robot.displayName}
            </Button>
          ),
        },
        { title: "真实机器人序列号", dataIndex: "serialNo", key: "serialNo" },
        {
          title: "绑定模型",
          key: "model",
          render: (_value, robot) =>
            modelLabel(
              bootstrapById.get(robot.id)?.effectiveModelBinding
                ?.robotModelVersionId ?? null,
              versionLabels,
            ),
        },
        {
          title: "生命周期",
          key: "lifecycle",
          render: (_value, robot) => (
            <StatusTag
              status={robot.lifecycle}
              label={lifecycleLabels[robot.lifecycle]}
              known={robot.lifecycle !== "UNKNOWN"}
              tone={robot.lifecycle === "ACTIVE" ? "success" : "warning"}
            />
          ),
        },
        {
          title: "连接状态",
          key: "connectivity",
          render: (_value, robot) => (
            <StatusTag
              status={robot.connectivity}
              known={robot.connectivity !== "UNKNOWN"}
              tone={robot.connectivity === "ONLINE" ? "success" : "neutral"}
            />
          ),
        },
        { title: "实例 ID", dataIndex: "id", key: "id" },
      ]}
    />
  );

  if (!canRead) return <PageState state="forbidden" label="机器人实例" />;

  return (
    <main className={styles.page} data-page-id="P15">
      <StandardPageScaffold
        header={{
          title: "机器人实例",
          breadcrumbs: [
            { key: "assets", label: "治理与资产" },
            { key: "robots", label: "机器人实例" },
          ],
          actions: (
            <Flex gap={8} wrap>
              <Button
                icon={<RefreshCw aria-hidden="true" size={16} />}
                loading={robots.isFetching}
                onClick={() => void robots.refetch()}
              >
                刷新
              </Button>
              {canManage && canBindModel ? (
                <Button
                  type="primary"
                  icon={<Plus aria-hidden="true" size={16} />}
                  onClick={() => setCreateOpen(true)}
                >
                  新建实例
                </Button>
              ) : null}
            </Flex>
          ),
        }}
        summary={
          <div className={styles.summary}>
            <div>
              <Bot aria-hidden="true" />
              <span>实例总数</span>
              <strong>{items.length}</strong>
            </div>
            <div>
              <Cable aria-hidden="true" />
              <span>在线</span>
              <strong>
                {items.filter((item) => item.connectivity === "ONLINE").length}
              </strong>
            </div>
            <div>
              <Boxes aria-hidden="true" />
              <span>已绑定模型</span>
              <strong>
                {
                  [...bootstrapById.values()].filter(
                    (item) => item.effectiveModelBinding,
                  ).length
                }
              </strong>
            </div>
          </div>
        }
        filters={
          <div className={styles.filters}>
            <Input.Search
              allowClear
              aria-label="搜索机器人实例"
              placeholder="搜索名称或序列号"
              onSearch={setQuery}
            />
            <Select
              aria-label="生命周期筛选"
              value={lifecycle}
              onChange={setLifecycle}
              options={[
                { label: "全部生命周期", value: "" },
                ...(
                  [
                    "DRAFT",
                    "ACTIVE",
                    "MAINTENANCE",
                    "DISABLED",
                    "RETIRED",
                  ] as const
                ).map((value) => ({ label: lifecycleLabels[value], value })),
              ]}
            />
          </div>
        }
        state={
          robots.isPending ? (
            <PageState state="loading" label="机器人实例" />
          ) : robots.isError ? (
            <PageState
              state="error"
              label="机器人实例"
              onRetry={() => void robots.refetch()}
            />
          ) : items.length === 0 ? (
            <PageState
              state={query || lifecycle ? "filtered-empty" : "empty"}
              label="机器人实例"
              action={
                canManage && canBindModel ? (
                  <Button type="primary" onClick={() => setCreateOpen(true)}>
                    新建实例
                  </Button>
                ) : undefined
              }
            />
          ) : (
            table
          )
        }
      />

      <EntityDrawer
        open={selectedId !== null}
        title={selected?.displayName ?? "机器人实例详情"}
        loading={detail.isPending}
        width={420}
        onClose={() => setSelectedId(null)}
      >
        {detail.isError ? (
          <PageState
            state="error"
            label="机器人实例详情"
            onRetry={() => void detail.refetch()}
          />
        ) : selected ? (
          <Space orientation="vertical" size="middle" className={styles.detail}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="实例 ID">
                {selected.id}
              </Descriptions.Item>
              <Descriptions.Item label="序列号">
                {selected.serialNo}
              </Descriptions.Item>
              <Descriptions.Item label="生命周期">
                {lifecycleLabels[selected.lifecycle]}
              </Descriptions.Item>
              <Descriptions.Item label="连接状态">
                {selected.connectivity.state}
              </Descriptions.Item>
              <Descriptions.Item label="绑定模型">
                {modelLabel(
                  selected.effectiveModelBinding?.robotModelVersionId ?? null,
                  versionLabels,
                )}
              </Descriptions.Item>
              <Descriptions.Item label="上传格式">
                <Tag>按每次上传识别</Tag>
              </Descriptions.Item>
            </Descriptions>
            <Flex gap={8} wrap>
              <Button href="/ingest/sources?sourceType=ROBOT">
                查看数据源与上传身份
              </Button>
              {canManage ? (
                <Button
                  icon={<Pencil size={15} />}
                  onClick={() => {
                    setRenameValue(selected.displayName);
                    setRenameOpen(true);
                  }}
                >
                  编辑名称
                </Button>
              ) : null}
              {canManage &&
              lifecycleTransitions[selected.lifecycle].length > 0 ? (
                <Button onClick={() => setLifecycleOpen(true)}>
                  变更生命周期
                </Button>
              ) : null}
              {canManage && canBindModel ? (
                <Button onClick={() => setBindOpen(true)}>重新绑定模型</Button>
              ) : null}
              {canManage ? (
                <Button
                  danger
                  icon={<Trash2 size={15} />}
                  disabled={
                    selected.lifecycle !== "DRAFT" ||
                    Boolean(selected.effectiveModelBinding)
                  }
                  onClick={() => setDeleteOpen(true)}
                >
                  删除实例
                </Button>
              ) : null}
            </Flex>
            {canManage &&
            (selected.lifecycle !== "DRAFT" ||
              selected.effectiveModelBinding) ? (
              <Alert
                type="info"
                showIcon
                title="该实例已进入业务链路，不能直接删除"
                description="只有未绑定模型、未关联数据源或上传身份的草稿实例可以永久删除；已使用的实例请通过“变更生命周期”停用或退役，以保留上传和审计记录。"
              />
            ) : null}
          </Space>
        ) : null}
      </EntityDrawer>

      <Modal
        open={createOpen}
        title="新建机器人实例"
        okText={existingSerial ? "查看已有实例" : "创建并绑定模型"}
        cancelText="取消"
        confirmLoading={createPending}
        okButtonProps={{
          disabled:
            !existingSerial &&
            (!createName.trim() || !createSerial.trim() || !createModelVersion),
        }}
        onCancel={closeCreate}
        onOk={() => void submitCreate()}
      >
        <div className={styles.form}>
          {createFlowError ? (
            <Alert type="error" showIcon title={createFlowError} />
          ) : null}
          <Alert
            type="info"
            showIcon
            title="一个实例对应一台真实机器人"
            description="实例只绑定机器人模型，不绑定 LeRobot、MCAP 等数据格式；格式在每次上传时识别。"
          />
          <label>
            <span>实例名称</span>
            <Input
              value={createName}
              onChange={(event) => setCreateName(event.target.value)}
            />
          </label>
          <label>
            <span>真实机器人序列号</span>
            <Input
              value={createSerial}
              onChange={(event) => setCreateSerial(event.target.value)}
            />
          </label>
          {existingSerial ? (
            <Alert
              type="warning"
              showIcon
              title={`序列号已对应实例“${existingSerial.displayName}”`}
              description="不会重复创建；确认后打开已有实例。"
            />
          ) : null}
          <label>
            <span>机器人模型</span>
            <Select
              showSearch
              optionFilterProp="label"
              value={createModelVersion || undefined}
              placeholder="请选择已发布模型"
              options={publishedModelOptions}
              onChange={setCreateModelVersion}
            />
          </label>
          {publishedModelOptions.length === 0 ? (
            <Alert
              type="warning"
              showIcon
              title="暂无已发布机器人模型"
              action={
                <Button href="/settings/robot-models">管理机器人模型</Button>
              }
            />
          ) : null}
        </div>
      </Modal>

      <Modal
        open={renameOpen}
        title="编辑实例名称"
        okText="保存"
        confirmLoading={updateRobot.isPending}
        onCancel={() => setRenameOpen(false)}
        onOk={() =>
          selected &&
          updateRobot.mutate(
            {
              robotId: selected.id,
              etag: selected.etag,
              displayName: renameValue.trim(),
            },
            { onSuccess: () => setRenameOpen(false) },
          )
        }
        okButtonProps={{ disabled: !renameValue.trim() }}
      >
        {updateRobot.error ? (
          <Alert
            type="error"
            showIcon
            title={operationError(updateRobot.error)}
          />
        ) : null}
        <Input
          aria-label="实例名称"
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
        />
      </Modal>

      <Modal
        open={lifecycleOpen}
        title="变更机器人生命周期"
        okText="确认变更"
        confirmLoading={transitionRobot.isPending}
        onCancel={() => setLifecycleOpen(false)}
        onOk={() =>
          selected &&
          lifecycleTarget &&
          transitionRobot.mutate(
            {
              robotId: selected.id,
              etag: selected.etag,
              lifecycleStatus: lifecycleTarget,
              reason: lifecycleReason.trim(),
            },
            {
              onSuccess: () => {
                setLifecycleOpen(false);
                setLifecycleTarget("");
                setLifecycleReason("");
              },
            },
          )
        }
        okButtonProps={{
          disabled: !lifecycleTarget || !lifecycleReason.trim(),
        }}
      >
        <div className={styles.form}>
          {transitionRobot.error ? (
            <Alert
              type="error"
              showIcon
              title={operationError(transitionRobot.error)}
            />
          ) : null}
          <label>
            <span>目标状态</span>
            <Select
              value={lifecycleTarget || undefined}
              options={(selected
                ? lifecycleTransitions[selected.lifecycle]
                : []
              ).map((value) => ({ value, label: lifecycleLabels[value] }))}
              onChange={setLifecycleTarget}
            />
          </label>
          <label>
            <span>变更原因</span>
            <Input.TextArea
              rows={3}
              value={lifecycleReason}
              onChange={(event) => setLifecycleReason(event.target.value)}
            />
          </label>
        </div>
      </Modal>

      <Modal
        open={bindOpen}
        title="重新绑定机器人模型"
        okText="绑定"
        confirmLoading={bindModel.isPending}
        onCancel={() => setBindOpen(false)}
        onOk={() =>
          selected &&
          bindVersion &&
          bindModel.mutate(
            {
              versionId: bindVersion,
              robotId: selected.id,
              robotEtag: selected.etag,
              idempotencyKey: createMutationIntentKey(),
            },
            {
              onSuccess: () => {
                setBindOpen(false);
                setBindVersion("");
                void detail.refetch();
              },
            },
          )
        }
        okButtonProps={{ disabled: !bindVersion }}
      >
        <div className={styles.form}>
          {bindModel.error ? (
            <Alert
              type="error"
              showIcon
              title={operationError(bindModel.error)}
            />
          ) : null}
          <label>
            <span>已发布模型</span>
            <Select
              showSearch
              optionFilterProp="label"
              value={bindVersion || undefined}
              options={publishedModelOptions}
              onChange={setBindVersion}
            />
          </label>
        </div>
      </Modal>

      <Modal
        open={deleteOpen}
        title="删除草稿机器人实例"
        okText="删除"
        okButtonProps={{ danger: true }}
        confirmLoading={deleteRobot.isPending}
        onCancel={() => setDeleteOpen(false)}
        onOk={() =>
          selected &&
          deleteRobot.mutate(selected.id, {
            onSuccess: () => {
              setDeleteOpen(false);
              setSelectedId(null);
            },
          })
        }
      >
        {deleteRobot.error ? (
          <Alert
            type="error"
            showIcon
            title={operationError(deleteRobot.error)}
          />
        ) : null}
        <p>仅未绑定模型、未被数据源或上传身份使用的草稿实例可以删除。</p>
      </Modal>
    </main>
  );
}

export default Component;
