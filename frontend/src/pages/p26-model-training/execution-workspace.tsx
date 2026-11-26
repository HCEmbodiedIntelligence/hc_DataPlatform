import { useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Pagination,
  Progress,
  Select,
  Table,
  Tag,
} from "antd";
import {
  readTraining,
  type ExecutionKind,
  type ExecutionRun,
} from "../../features/model-training/api";
import { isDomainError } from "../../shared/api/domain-error";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import type { Scope } from "../../shared/scope/model";
import { ExecutionForm } from "./execution-form";

export const executionLabels: Record<ExecutionKind, string> = {
  supervised: "常规训练",
  offline_rl: "离线强化",
  online_rl: "在线强化",
  simulation: "仿真评测",
};
const statusLabels = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function ExecutionWorkspace({
  scope,
  kind,
  active,
  canStart,
  legacy,
}: {
  scope: Scope;
  kind: ExecutionKind;
  active: boolean;
  canStart: boolean;
  legacy: ReactNode;
}) {
  const [profilePage, setProfilePage] = useState(1),
    [runPage, setRunPage] = useState(1);
  const [chosen, setChosen] = useState(""),
    [legacyMode, setLegacyMode] = useState(false);
  const profiles = useScopedQuery(
    scope,
    "training",
    "execution/profiles",
    [kind, profilePage],
    {
      enabled: active && !legacyMode,
      retry: false,
      queryFn: async ({ signal }) => {
        try {
          return await readTraining(
            scope,
            "execution/profiles",
            { kind, page: profilePage, page_size: 50 },
            signal,
          );
        } catch (error) {
          if (isDomainError(error) && error.httpStatus === 404) return null;
          throw error;
        }
      },
    },
  );
  const unsupported = profiles.data === null;
  const runs = useScopedQuery(
    scope,
    "training",
    "execution/runs",
    [kind, runPage],
    {
      enabled: active && !legacyMode && !!profiles.data && !unsupported,
      queryFn: ({ signal }) =>
        readTraining(
          scope,
          "execution/runs",
          { kind, page: runPage, page_size: 10 },
          signal,
        ),
      refetchInterval: active && !legacyMode ? 5000 : false,
    },
  );
  if (unsupported)
    return legacy ?? <Empty description={executionLabels[kind] + "尚未接入"} />;
  if (legacyMode)
    return (
      <>
        <Button onClick={() => setLegacyMode(false)}>返回执行方案</Button>
        {legacy}
      </>
    );
  const profileError = profiles.error && (
    <Alert
      type="error"
      title="执行方案读取失败"
      description={profiles.error.message}
      action={<Button onClick={() => void profiles.refetch()}>重试</Button>}
    />
  );
  if (!profiles.data) return profileError || <Card loading />;
  const options = profiles.data.items;
  const profile =
    options.find((p) => p.id === chosen) ??
    (options.length === 1 ? options[0] : undefined);
  return (
    <>
      <Card
        variant="borderless"
        title="任务配置"
        extra={
          legacy && (
            <Button type="link" onClick={() => setLegacyMode(true)}>
              旧版训练
            </Button>
          )
        }
      >
        {profileError}
        {options.length ? (
          <Form.Item label="执行方案">
            <Select
              aria-label="执行方案"
              showSearch
              optionFilterProp="label"
              style={{ width: "100%" }}
              placeholder="选择模型与环境方案"
              value={profile?.id}
              onChange={setChosen}
              options={options.map((p) => ({ value: p.id, label: p.label }))}
            />
          </Form.Item>
        ) : (
          <Empty description="当前类型暂无可用方案">
            <Button onClick={() => void profiles.refetch()}>重新检查</Button>
          </Empty>
        )}
        {profiles.data.total > 50 && (
          <Pagination
            size="small"
            current={profilePage}
            pageSize={50}
            total={profiles.data.total}
            showSizeChanger={false}
            onChange={(p) => {
              setProfilePage(p);
              setChosen("");
            }}
          />
        )}
        {profile && (
          <ExecutionForm
            key={profile.id}
            scope={scope}
            profile={profile}
            canStart={canStart && !profiles.error}
            active={active}
            onAccepted={() => setRunPage(1)}
          />
        )}
      </Card>
      <Card
        variant="borderless"
        title="任务记录"
        extra={
          <Button loading={runs.isFetching} onClick={() => void runs.refetch()}>
            刷新状态
          </Button>
        }
      >
        {runs.error && (
          <Alert
            type="error"
            title="任务状态刷新失败"
            description={
              runs.data ? "表格保留上次成功获取的状态。" : runs.error.message
            }
          />
        )}
        <Table<ExecutionRun>
          rowKey="id"
          loading={runs.isLoading}
          dataSource={runs.data?.items ?? []}
          scroll={{ x: 850 }}
          locale={{
            emptyText: runs.error ? "无法获取任务列表" : "当前类型暂无任务",
          }}
          pagination={{
            current: runPage,
            pageSize: 10,
            total: runs.data?.total ?? 0,
            showSizeChanger: false,
            onChange: setRunPage,
            hideOnSinglePage: true,
          }}
          expandable={{
            expandedRowRender: (r) => (
              <Descriptions
                size="small"
                column={1}
                items={[
                  { key: "profile", label: "方案版本", children: r.profile_id },
                  {
                    key: "parameters",
                    label: "实际参数",
                    children: (
                      <pre
                        style={{
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                        }}
                      >
                        {JSON.stringify(r.parameters, null, 2)}
                      </pre>
                    ),
                  },
                  {
                    key: "message",
                    label: "状态说明",
                    children: r.status_message || "—",
                  },
                  {
                    key: "artifacts",
                    label: "产物",
                    children: r.artifacts.length ? (
                      <ul>
                        {r.artifacts.map((a) => (
                          <li key={a.kind + ":" + a.id}>
                            {a.label} · {a.kind} · {a.id}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      "暂无产物"
                    ),
                  },
                ]}
              />
            ),
          }}
          columns={[
            { title: "任务 ID", dataIndex: "id" },
            {
              title: "方案",
              render: (_, r) =>
                options.find((p) => p.id === r.profile_id)?.label ??
                r.profile_id,
            },
            {
              title: "数据版本",
              render: (_, r) => r.dataset_version_id ?? "—",
            },
            {
              title: "状态",
              render: (_, r) => <Tag>{statusLabels[r.status]}</Tag>,
            },
            {
              title: "进度",
              render: (_, r) =>
                r.progress_percent == null ? (
                  "—"
                ) : (
                  <Progress
                    percent={r.progress_percent}
                    status={r.status === "failed" ? "exception" : "normal"}
                    size="small"
                  />
                ),
            },
            {
              title: "提交时间",
              render: (_, r) => new Date(r.created_at).toLocaleString(),
            },
          ]}
        />
      </Card>
    </>
  );
}
