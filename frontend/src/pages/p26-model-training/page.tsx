import { useRef, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Progress,
  Row,
  Select,
  Switch,
  Table,
  Tabs,
  Tag,
} from "antd";
import { useQueryClient } from "@tanstack/react-query";
import {
  readTraining,
  startTraining,
  type TrainingOption,
  type TrainingRun,
  type TrainingStart,
} from "../../features/model-training/api";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import {
  invalidateScopedDomain,
  useScopedMutation,
} from "../../shared/api/use-scoped-mutation";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import type { Scope } from "../../shared/scope/model";
import { StandardPageScaffold } from "../../shared/ui/layout";
import layout from "../../shared/ui/workspace.module.css";

import { ExecutionWorkspace, executionLabels } from "./execution-workspace";
import type { ExecutionKind } from "../../features/model-training/api";

const states = {
  queued: ["排队中", "default"],
  running: ["训练中", "processing"],
  succeeded: ["已完成", "success"],
  failed: ["失败", "error"],
  cancelled: ["已取消", "default"],
} as const;

function TrainingEntry(props: { scope: Scope | null; children: ReactNode }) {
  const availability = useScopedQuery(
    props.scope,
    "training",
    "availability",
    {},
    {
      queryFn: ({ signal }) =>
        readTraining(props.scope!, "availability", {}, signal),
    },
  );
  if (!props.scope) return <Empty description="请选择项目和区域" />;
  if (availability.error)
    return (
      <Alert
        type="error"
        showIcon
        title="无法读取训练服务配置"
        description={availability.error.message}
        action={
          <Button onClick={() => void availability.refetch()}>重试</Button>
        }
      />
    );
  if (!availability.data) return <Card loading />;
  if (!availability.data.configured)
    return (
      <Empty description="尚未接入训练服务。接入后可选择数据集并查看训练任务。">
        <Button
          loading={availability.isFetching}
          onClick={() => void availability.refetch()}
        >
          重新检查
        </Button>
      </Empty>
    );
  return <>{props.children}</>;
}

function TrainingWorkspace({
  active,
  scope,
  canStart,
}: {
  scope: Scope | null;
  canStart: boolean;
  active: boolean;
}) {
  const [selection, setSelection] = useState<
    Partial<
      Pick<TrainingStart, "dataset_id" | "model_id" | "card_id" | "config_id">
    >
  >({});
  const [overrides, setOverrides] = useState<
    Record<string, string | number | boolean | null>
  >({});
  const [page, setPage] = useState(1);
  const submitting = useRef(false);
  const retry = useRef<{ payload: string; key: string } | null>(null);
  const client = useQueryClient();

  const datasets = useScopedQuery(
    scope,
    "training",
    "datasets",
    {},
    {
      queryFn: ({ signal }) => readTraining(scope!, "datasets", {}, signal),
    },
  );
  const dataset = datasets.data?.items.find(
    (x) => x.id === selection.dataset_id,
  );
  const models = useScopedQuery(scope, "training", "models", dataset?.id, {
    enabled: !!dataset,
    queryFn: ({ signal }) =>
      readTraining(scope!, "models", { dataset_id: dataset!.id }, signal),
  });
  const model = models.data?.items.find((x) => x.id === selection.model_id);
  const cards = useScopedQuery(
    scope,
    "training",
    "cards",
    [dataset?.id, model?.id],
    {
      enabled: !!dataset && !!model,
      queryFn: ({ signal }) =>
        readTraining(
          scope!,
          "cards",
          { dataset_id: dataset!.id, model_id: model!.id },
          signal,
        ),
    },
  );
  const card = cards.data?.items.find((x) => x.id === selection.card_id);
  const configs = useScopedQuery(
    scope,
    "training",
    "configs",
    [dataset?.id, model?.id, card?.id],
    {
      enabled: !!dataset && !!model && !!card,
      queryFn: ({ signal }) =>
        readTraining(
          scope!,
          "configs",
          { dataset_id: dataset!.id, model_id: model!.id, card_id: card!.id },
          signal,
        ),
    },
  );
  const config = configs.data?.items.find((x) => x.id === selection.config_id);
  const runs = useScopedQuery(scope, "training", "runs", page, {
    queryFn: ({ signal }) =>
      readTraining(scope!, "runs", { page, page_size: 10 }, signal),
    enabled: active,
    refetchInterval: active ? 5000 : false,
  });
  const start = useScopedMutation(scope, startTraining, {
    onSuccess: (_run, _intent, binding) => {
      retry.current = null;
      setSelection((current) => ({ ...current, config_id: undefined }));
      setOverrides({});
      setPage(1);
      void invalidateScopedDomain(client, binding, "training");
    },
  });

  function choose(next: typeof selection) {
    setSelection(next);
    setOverrides({});
    start.reset();
  }

  function field(
    label: string,
    query: typeof datasets | typeof configs,
    selected: TrainingOption | undefined,
    enabled: boolean,
    onChange: (id: string) => void,
  ) {
    return (
      <Col xs={24} sm={12} xl={6}>
        <Form.Item label={label} htmlFor={label}>
          <Select
            id={label}
            value={selected?.id}
            disabled={!enabled || !canStart || start.isPending}
            loading={query.isFetching}
            placeholder={label}
            notFoundContent={query.isFetching ? "加载中" : "暂无可选项"}
            options={query.data?.items.map((x) => ({
              value: x.id,
              label: x.label,
            }))}
            onChange={onChange}
          />
        </Form.Item>
      </Col>
    );
  }

  const optionError = [datasets, models, cards, configs].find(
    (q) => q.isEnabled && q.error,
  );
  const parameters = Object.fromEntries(
    Object.entries(config?.parameters ?? {}).map(([key, value]) => [
      key,
      Object.hasOwn(overrides, key) ? overrides[key] : value,
    ]),
  );
  const valid = Object.values(parameters).every((v) => v != null);

  async function submit() {
    if (
      !dataset ||
      !model ||
      !card ||
      !config ||
      !valid ||
      optionError ||
      !canStart ||
      submitting.current
    )
      return;
    const command: TrainingStart = {
      dataset_id: dataset.id,
      model_id: model.id,
      card_id: card.id,
      config_id: config.id,
      parameters: parameters as TrainingStart["parameters"],
    };
    const payload = JSON.stringify([
      dataset.id,
      model.id,
      card.id,
      config.id,
      Object.entries(command.parameters).sort(([left], [right]) =>
        left.localeCompare(right),
      ),
    ]);
    if (retry.current?.payload !== payload)
      retry.current = { payload, key: crypto.randomUUID() };
    submitting.current = true;
    try {
      await start.mutateAsync({ command, key: retry.current.key });
    } catch {
      // The mutation exposes its error below. Preserve the key for an uncertain submission.
    } finally {
      submitting.current = false;
    }
  }

  return (
    <>
      <Card variant="borderless" title="训练配置">
        <Form layout="vertical" onFinish={submit}>
          <Row gutter={16}>
            {field("选择数据", datasets, dataset, true, (id) =>
              choose({ dataset_id: id }),
            )}
            {field("选择模型", models, model, !!dataset, (id) =>
              choose({ dataset_id: dataset!.id, model_id: id }),
            )}
            {field("选择训练卡", cards, card, !!model, (id) =>
              choose({
                dataset_id: dataset!.id,
                model_id: model!.id,
                card_id: id,
              }),
            )}
            {field("选择参数配置", configs, config, !!card, (id) =>
              choose({ ...selection, config_id: id }),
            )}
          </Row>
          {config && (
            <Row gutter={16}>
              {Object.entries(config.parameters).map(([key, original]) => {
                const value = parameters[key];
                const update = (next: typeof original | null) =>
                  setOverrides((current) => ({ ...current, [key]: next }));
                const id = `training-param-${key}`;
                return (
                  <Col xs={24} sm={12} xl={6} key={key}>
                    <Form.Item label={key} htmlFor={id}>
                      {typeof original === "boolean" ? (
                        <Switch
                          id={id}
                          checked={value === true}
                          onChange={update}
                          disabled={!canStart || start.isPending}
                        />
                      ) : typeof original === "number" ? (
                        <InputNumber
                          id={id}
                          style={{ width: "100%" }}
                          value={typeof value === "number" ? value : null}
                          onChange={update}
                          disabled={!canStart || start.isPending}
                        />
                      ) : (
                        <Input
                          id={id}
                          value={String(value ?? "")}
                          onChange={(e) => update(e.target.value)}
                          disabled={!canStart || start.isPending}
                        />
                      )}
                    </Form.Item>
                  </Col>
                );
              })}
            </Row>
          )}
          {optionError && (
            <Alert
              type="error"
              showIcon
              title="训练选项加载失败"
              description={optionError.error?.message}
              action={
                <Button onClick={() => void optionError.refetch()}>重试</Button>
              }
            />
          )}
          {start.error && (
            <Alert
              type="error"
              showIcon
              title="训练提交未确认"
              description={start.error.message}
              action={
                <Button
                  onClick={() => {
                    for (const query of [
                      datasets,
                      models,
                      cards,
                      configs,
                      runs,
                    ]) {
                      if (query.isEnabled) void query.refetch();
                    }
                  }}
                >
                  刷新选项和任务
                </Button>
              }
            />
          )}
          {start.data && (
            <Alert
              type="success"
              showIcon
              title={`训练端已受理任务 ${start.data.id}`}
            />
          )}
          <Button
            type="primary"
            htmlType="submit"
            loading={start.isPending}
            disabled={!canStart || !config || !valid || !!optionError}
          >
            开始训练
          </Button>
        </Form>
      </Card>
      <Card
        variant="borderless"
        title="训练任务"
        extra={
          <Button
            disabled={!scope}
            loading={runs.isFetching}
            onClick={() => void runs.refetch()}
          >
            刷新状态
          </Button>
        }
      >
        {runs.error && (
          <Alert
            type="error"
            showIcon
            title="训练状态刷新失败"
            description={
              runs.data ? "表格保留上次成功获取的状态。" : runs.error.message
            }
          />
        )}
        <Table<TrainingRun>
          rowKey="id"
          dataSource={runs.data?.items ?? []}
          loading={runs.isLoading}
          scroll={{ x: 840 }}
          locale={{
            emptyText: runs.error ? "无法获取任务列表" : "暂无训练任务",
          }}
          pagination={{
            current: page,
            pageSize: 10,
            total: runs.data?.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
            hideOnSinglePage: true,
          }}
          expandable={{
            expandedRowRender: (run) => (
              <Descriptions
                size="small"
                column={1}
                items={[
                  {
                    key: "config",
                    label: "参数配置",
                    children: run.config.label,
                  },
                  {
                    key: "params",
                    label: "训练参数",
                    children: JSON.stringify(run.parameters),
                  },
                  {
                    key: "time",
                    label: "提交时间",
                    children: new Date(run.created_at).toLocaleString(),
                  },
                ]}
              />
            ),
          }}
          columns={[
            { title: "任务 ID", dataIndex: "id" },
            { title: "数据", render: (_, r) => r.dataset.label },
            { title: "模型", render: (_, r) => r.model.label },
            { title: "训练卡", render: (_, r) => r.card.label },
            {
              title: "状态",
              render: (_, r) => (
                <Tag color={states[r.status][1]}>{states[r.status][0]}</Tag>
              ),
            },
            {
              title: "进度",
              width: 140,
              render: (_, r) =>
                r.progress_percent == null ? (
                  "—"
                ) : (
                  <Progress
                    size="small"
                    percent={r.progress_percent}
                    status={r.status === "failed" ? "exception" : "normal"}
                  />
                ),
            },
            { title: "状态说明", render: (_, r) => r.status_message || "—" },
          ]}
        />
      </Card>
    </>
  );
}

export function Component() {
  const scope = useShellStore((s) => s.scope);
  const scopeKey = useShellStore((s) => s.scopeKey);
  const revision = useShellStore((s) => s.sessionRevision);
  const capabilities = useCapabilities();
  return (
    <div className={layout.page}>
      <StandardPageScaffold header={{ title: "模型训练与仿真" }}>
        {scope && !capabilities.has("training.read") ? (
          <Alert
            type="info"
            title={
              capabilities.loading ? "正在加载权限" : "当前项目没有训练查看权限"
            }
          />
        ) : (
          <TrainingTabs
            key={`${scopeKey}:${revision}`}
            scope={scope?.projectId && scope.regionCode ? scope : null}
            canStart={capabilities.has("training.create")}
          />
        )}
      </StandardPageScaffold>
    </div>
  );
}

function TrainingTabs({
  scope,
  canStart,
}: {
  scope: Scope | null;
  canStart: boolean;
}) {
  const [kind, setKind] = useState<ExecutionKind>("supervised");
  return (
    <Tabs
      activeKey={kind}
      onChange={(key) => setKind(key as ExecutionKind)}
      items={(Object.keys(executionLabels) as ExecutionKind[]).map((key) => ({
        key,
        label: executionLabels[key],
        children: (
          <TrainingEntry scope={scope}>
            {scope && (
              <ExecutionWorkspace
                scope={scope}
                kind={key}
                active={kind === key}
                canStart={canStart}
                legacy={
                  key === "supervised" ? (
                    <TrainingWorkspace
                      scope={scope}
                      canStart={canStart}
                      active={kind === key}
                    />
                  ) : null
                }
              />
            )}
          </TrainingEntry>
        ),
      }))}
    />
  );
}
