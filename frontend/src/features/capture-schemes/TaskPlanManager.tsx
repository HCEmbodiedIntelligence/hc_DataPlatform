import { Alert, Button, Form, Spin } from "antd";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { CollectionTaskScope } from "../collection-tasks/api";
import {
  useCapabilities,
  useOrganizationCapabilities,
} from "../../shared/auth/use-capabilities";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import {
  invalidateScopedDomain,
  scopedMutationKey,
  useScopedMutation,
} from "../../shared/api/use-scoped-mutation";
import { captureApi, taskPlanRequest, useCaptureQuery } from "./api";
import { captureError } from "./presentation";
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
  const [selection, setSelection] = useState<TaskPlanSelection | null>(null);
  const [candidate, setCandidate] = useState<SetTaskPlan | null>(null);
  const client = useQueryClient();
  const save = useScopedMutation(
    scope,
    (_scope, command: { kind: "replace"; plan: SetTaskPlan }) =>
      taskPlanRequest(scope, taskId, command),
    {
      onSuccess: (data, _variables, binding) => {
        client.setQueryData(
          scopedMutationKey(binding, "capture-schemes", "task-plan", taskId),
          data,
        );
        setSelection(null);
        setCandidate(null);
        void invalidateScopedDomain(client, binding, "capture-schemes");
      },
    },
  );
  useEffect(() => {
    setSelection(null);
    setCandidate(null);
  }, [
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    taskId,
    plan?.revision,
  ]);
  if (!permissions.has("upload.read"))
    return <Alert type="info" title="查看任务采集方案需要采集读取权限" />;
  if (query.isPending) return <Spin size="small" />;
  const writable =
    active &&
    permissions.has("upload.manage") &&
    organization.has("robot.read") &&
    !query.error;
  const savedSelection: TaskPlanSelection = plan
    ? {
        schemeId: plan.scheme.scheme_id,
        versionKey: `${plan.scheme.scheme_id}:${plan.scheme.version}:${plan.scheme.content_sha256}`,
      }
    : { versionKey: "" };
  const changed =
    selection !== null && selection.versionKey !== savedSelection.versionKey;
  return (
    <section aria-label="已保存的任务采集计划">
      {query.error && (
        <Alert
          type="error"
          title={captureError(query.error)}
          action={
            <Button onClick={() => void query.refetch()}>重新读取计划</Button>
          }
        />
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
      {writable ? (
        <TaskPlanSelector
          organizationId={scope.organizationId}
          value={selection ?? savedSelection}
          selectedName={selection ? undefined : version.data?.name}
          onSelectionChange={(value) => {
            setSelection(value);
            setCandidate(null);
            save.reset();
          }}
          onChange={setCandidate}
          disabled={save.isPending || query.isFetching}
          action={
            changed && (
              <Button
                type="primary"
                aria-label="保存任务采集方案"
                loading={save.isPending}
                disabled={query.isFetching || !candidate}
                onClick={() =>
                  candidate &&
                  save.mutate({
                    kind: "replace",
                    plan: {
                      ...candidate,
                      expected_revision: plan?.revision ?? 0,
                    },
                  })
                }
              >
                保存
              </Button>
            )
          }
        />
      ) : (
        <Form.Item label="采集方案">
          {version.data?.name ?? (plan ? "方案名称暂不可用" : "未配置")}
        </Form.Item>
      )}
    </section>
  );
}
