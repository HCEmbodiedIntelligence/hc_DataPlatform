import { useRef, useState } from "react";
import { Alert, Button, Descriptions, Form, Select } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import {
  readTraining,
  startExecution,
  type ExecutionProfile,
  type ExecutionStart,
} from "../../features/model-training/api";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import {
  invalidateScopedDomain,
  useScopedMutation,
} from "../../shared/api/use-scoped-mutation";
import type { Scope } from "../../shared/scope/model";
import {
  object,
  parameterDefaults,
  schemaIssue,
  valueIssue,
  type JsonObject,
} from "./parameters";
import { ParameterEditor } from "./parameter-editor";

export function ExecutionForm({
  scope,
  profile,
  canStart,
  active,
  onAccepted,
}: {
  scope: Scope;
  profile: ExecutionProfile;
  canStart: boolean;
  active: boolean;
  onAccepted: () => void;
}) {
  const schema = profile.parameters_schema;
  const [raw, setRaw] = useState(() =>
    JSON.stringify(parameterDefaults(schema), null, 2),
  );
  const [dataset, setDataset] = useState<string>(),
    [accepted, setAccepted] = useState<string>();
  const lock = useRef(false),
    intent = useRef<{ payload: string; key: string } | null>(null);
  const client = useQueryClient();
  const start = useScopedMutation(scope, startExecution, {
    onSuccess: (r, _i, binding) => {
      setAccepted(r.id);
      intent.current = null;
      onAccepted();
      void invalidateScopedDomain(client, binding, "training");
    },
  });
  const usesData = profile.dataset_requirement !== "none";
  const datasets = useScopedQuery(
    scope,
    "training",
    "datasets",
    {},
    {
      enabled: active && usesData,
      queryFn: ({ signal }) => readTraining(scope, "datasets", {}, signal),
    },
  );
  const selectedDataset = datasets.data?.items.find((d) => d.id === dataset);
  const unsupported = schemaIssue(schema);
  let parameters: JsonObject = {},
    invalidJson = false;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!object(parsed)) invalidJson = true;
    else parameters = parsed;
  } catch {
    invalidJson = true;
  }
  const issue =
    unsupported ??
    (invalidJson ? "请输入有效的 JSON 对象" : valueIssue(schema, parameters));
  const disabled = !canStart || start.isPending;
  const valid =
    !issue &&
    (!dataset || !!selectedDataset) &&
    (profile.dataset_requirement !== "required" || !!selectedDataset);
  async function submit() {
    if (disabled || !valid || accepted || lock.current) return;
    const command: ExecutionStart = {
      profile_id: profile.id,
      dataset_version_id: selectedDataset?.id ?? null,
      parameters,
    };
    const payload = JSON.stringify(command);
    if (intent.current?.payload !== payload)
      intent.current = { payload, key: crypto.randomUUID() };
    lock.current = true;
    try {
      await start.mutateAsync({ command, key: intent.current.key });
    } catch {
      /* The mutation exposes the error and the retry keeps its key. */
    } finally {
      lock.current = false;
    }
  }
  return (
    <Form layout="vertical" onFinish={submit}>
      <Descriptions
        size="small"
        items={[
          { key: "p", label: "方案版本", children: profile.id },
          ...(profile.model_revision
            ? [
                {
                  key: "m",
                  label: "模型版本",
                  children: profile.model_revision,
                },
              ]
            : []),
          ...(profile.environment_revision
            ? [
                {
                  key: "e",
                  label: "环境版本",
                  children: profile.environment_revision,
                },
              ]
            : []),
        ]}
      />
      {usesData && (
        <Form.Item
          label="数据集版本"
          required={profile.dataset_requirement === "required"}
        >
          <Select
            aria-label="数据集版本"
            showSearch
            optionFilterProp="label"
            allowClear
            disabled={disabled}
            value={selectedDataset?.id}
            placeholder={
              profile.dataset_requirement === "required"
                ? "选择固定的数据版本"
                : "可选数据版本"
            }
            loading={datasets.isFetching}
            options={datasets.data?.items.map((d) => ({
              value: d.id,
              label: d.label,
            }))}
            onChange={(v) => {
              setDataset(v);
              setAccepted(undefined);
              start.reset();
            }}
          />
        </Form.Item>
      )}
      {usesData && datasets.error && (
        <Alert
          type="error"
          title="数据版本读取失败"
          description={datasets.error.message}
          action={<Button onClick={() => void datasets.refetch()}>重试</Button>}
        />
      )}
      <ParameterEditor
        schema={schema}
        raw={raw}
        values={parameters}
        disabled={disabled}
        invalidJson={invalidJson}
        onChange={(v) => {
          setRaw(v);
          setAccepted(undefined);
          start.reset();
        }}
      />
      {issue && (
        <Alert type={unsupported ? "error" : "info"} showIcon title={issue} />
      )}
      {start.error && (
        <Alert
          type="error"
          showIcon
          title="任务提交未确认"
          description={start.error.message}
        />
      )}
      {accepted && (
        <Alert type="success" showIcon title={"执行端已受理任务 " + accepted} />
      )}
      <Button
        type="primary"
        htmlType="submit"
        disabled={disabled || !valid || !!accepted}
        loading={start.isPending}
      >
        {profile.kind === "simulation" ? "开始评测" : "开始训练"}
      </Button>
      {accepted && (
        <Button type="link" onClick={() => setAccepted(undefined)}>
          新建相同任务
        </Button>
      )}
    </Form>
  );
}
