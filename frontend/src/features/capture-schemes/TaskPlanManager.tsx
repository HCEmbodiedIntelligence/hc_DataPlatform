import { Alert, Button, Space, Spin } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { CollectionTaskScope } from "../collection-tasks/api";
import {
  useCapabilities,
  useOrganizationCapabilities,
} from "../../shared/auth/use-capabilities";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import {
  invalidateScopedDomain,
  useScopedMutation,
} from "../../shared/api/use-scoped-mutation";
import { captureApi, taskPlanRequest, useCaptureQuery } from "./api";
import { captureError, dataSummary } from "./presentation";
import { TaskPlanSelector, type TaskPlanSelection } from "./TaskPlanSelector";
import type { SetTaskPlan } from "./contracts";

export function TaskPlanManager({
  scope,
  taskId,
  active,
}: {
  scope: CollectionTaskScope;
  taskId: string;
  active: boolean;
}) {
  const permissions = useCapabilities();
  const organization = useOrganizationCapabilities(scope.organizationId);
  const query = useScopedQuery(scope, "capture-schemes", "task-plan", taskId, {
    enabled: permissions.has("upload.read"),
    queryFn: ({ signal }) => taskPlanRequest(scope, taskId, undefined, signal),
    retry: false,
  });
  const plan = query.data?.plan;
  const version = useCaptureQuery(
    scope.organizationId,
    "exact-version",
    plan?.scheme,
    (signal) =>
      captureApi.version(
        scope.organizationId,
        plan!.scheme.scheme_id,
        plan!.scheme.version,
        signal,
      ),
    Boolean(plan) && organization.has("robot.read"),
  );
  const [operation, setOperation] = useState<"replace" | null>(null);
  const [selection, setSelection] = useState<TaskPlanSelection>({
    versionKey: "",
  });
  const [candidate, setCandidate] = useState<SetTaskPlan | null>(null);
  const client = useQueryClient();
  const save = useScopedMutation(
    scope,
    (_scope, command: { kind: "replace"; plan: SetTaskPlan }) =>
      taskPlanRequest(scope, taskId, command),
    {
      onSuccess: (_data, _variables, binding) => {
        setOperation(null);
        void invalidateScopedDomain(client, binding, "capture-schemes");
      },
    },
  );
  useEffect(() => {
    setOperation(null);
    setSelection({ versionKey: "" });
    setCandidate(null);
  }, [
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    taskId,
    plan?.revision,
  ]);
  function start() {
    save.reset();
    setSelection({ versionKey: "" });
    setCandidate(null);
    setOperation("replace");
  }
  if (!permissions.has("upload.read"))
    return <Alert type="info" title="查看任务采集方案需要采集读取权限" />;
  if (query.isPending) return <Spin size="small" />;
  const writable =
    active &&
    permissions.has("upload.manage") &&
    !query.isFetching &&
    !query.error;
  return (
    <section aria-label="已保存的任务采集计划">
      <h3>任务采集方案</h3>
      {query.error && (
        <Alert
          type="error"
          title={captureError(query.error)}
          action={
            <Button onClick={() => void query.refetch()}>重新读取计划</Button>
          }
        />
      )}
      {plan ? (
        <>
          <p>
            {version.data?.name ?? "采集方案"} · v{plan.scheme.version}
          </p>
          {version.data && <p>{dataSummary(version.data)}</p>}
          <Link
            to={`/settings/capture-schemes/${encodeURIComponent(plan.scheme.scheme_id)}`}
          >
            查看共享方案
          </Link>
          <p>使用同一采集方案的机器人均可执行此任务并上传数据。</p>
          <p>
            {plan.locked_at
              ? "已经开始采集，方案版本已固定。"
              : "尚未开始采集，可以显式更换方案。"}
          </p>
        </>
      ) : (
        !query.error && <Alert type="warning" title="任务采集方案待配置" />
      )}
      {save.error && (
        <Alert
          type="error"
          title={captureError(save.error)}
          action={
            <Button onClick={() => void query.refetch()}>重新读取计划</Button>
          }
        />
      )}
      {operation ? (
        <>
          <TaskPlanSelector
            organizationId={scope.organizationId}
            value={selection}
            onSelectionChange={setSelection}
            onChange={setCandidate}
            disabled={save.isPending}
          />
          <Space>
            <Button
              type="primary"
              loading={save.isPending}
              disabled={!writable || !candidate}
              onClick={() =>
                candidate &&
                save.mutate({
                  kind: operation,
                  plan: {
                    ...candidate,
                    expected_revision: plan?.revision ?? 0,
                  },
                })
              }
            >
              保存任务方案
            </Button>
            <Button
              disabled={save.isPending}
              onClick={() => setOperation(null)}
            >
              取消方案修改
            </Button>
          </Space>
        </>
      ) : (
        writable && (
          <Space>
            {!plan?.locked_at && (
              <Button onClick={start}>
                {plan ? "更换采集方案" : "配置采集方案"}
              </Button>
            )}
          </Space>
        )
      )}
    </section>
  );
}
