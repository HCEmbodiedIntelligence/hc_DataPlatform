import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Tag,
} from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import { annotationRoutes } from "../../features/annotation/routing";
import {
  claimRuntimeAnnotationTask,
  listRuntimeAnnotationTasks,
  type RuntimeAnnotationTask,
} from "../../features/annotation/runtime/runtime-annotation-adapter";
import {
  annotationTaskListKey,
  taskQueryKey,
} from "../../features/annotation/runtime/task-preload";
import {
  modelRequest,
  useModelScope,
  type Guide,
  type RunResult,
  type ModelScope,
} from "../../features/model-annotation/api";
import { request } from "../../shared/api/http-client";
import { useScopeQueryRevision } from "../../shared/api/use-scoped-query";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { StandardPageScaffold } from "../../shared/ui";

const newGuide: Guide = {
  name: "",
  revision: 0,
  instructions: "",
  prompt:
    "根据画面和标注规范，从本任务的候选标签中选择动作，标出每个动作的起止帧。无法判断的片段留空。",
};

export function Component() {
  const scope = useModelScope();
  const session = useScopeQueryRevision();
  return (
    <StandardPageScaffold
      header={{
        title: "模型标注",
        breadcrumbs: [
          { key: "production", label: "数据生产" },
          { key: "model", label: "模型标注" },
        ],
      }}
    >
      {scope ? (
        <Workspace
          key={JSON.stringify([scope, session])}
          scope={scope}
          session={session}
        />
      ) : (
        <Alert type="info" title="请先选择项目和区域" />
      )}
    </StandardPageScaffold>
  );
}

function Workspace({ scope, session }: { scope: ModelScope; session: string }) {
  const client = useQueryClient();
  const rights = useCapabilities();
  const actor = useShellStore((s) => s.principal?.actorId);
  const listKey = annotationTaskListKey(scope, session);
  const list = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) => listRuntimeAnnotationTasks(scope, signal),
  });
  const guideKey = ["model-annotation-guides", session, scope];
  const guides = useQuery({
    queryKey: guideKey,
    queryFn: ({ signal }) =>
      modelRequest<Guide[]>(scope, "/guides", { method: "GET", signal }),
  });
  const [collectionId, setCollectionId] = useState<string>();
  const [taskId, setTaskId] = useState<string>();
  const [guideName, setGuideName] = useState<string>();
  const [edit, setEdit] = useState<Guide | null>(null);
  const items = (list.data ?? []).filter(
    (task) =>
      task.data_ref && ["DRAFT", "NEEDS_REVISION"].includes(task.status),
  );
  const collectionTasks = new Map<string, string>();
  for (const item of items) {
    if (item.collection_task_id) {
      const code =
        item.collection_task_code?.replace(/^0+(?=\d)/, "") ??
        item.collection_task_id;
      collectionTasks.set(item.collection_task_id, `任务 ${code}`);
    }
  }
  const taskKey = ["model-annotation-task", session, scope, taskId];
  const detail = useQuery({
    queryKey: taskKey,
    enabled: !!taskId,
    queryFn: ({ signal }) =>
      request<RuntimeAnnotationTask>({
        scope,
        method: "GET",
        path: "/annotation-tasks/" + encodeURIComponent(taskId!),
        signal,
        cache: "no-store",
      }),
  });
  const task = detail.data;
  const guide = guides.data?.find((g) => g.name === guideName);
  const saveGuide = useMutation({
    mutationFn: (draft: Guide) =>
      modelRequest<Guide>(scope, "/guides", { method: "PUT", body: draft }),
    onSuccess: (saved) => {
      client.setQueryData<Guide[]>(guideKey, (current) => [
        ...(current ?? []).filter((g) => g.name !== saved.name),
        saved,
      ]);
      setGuideName(saved.name);
      setEdit(null);
    },
  });
  const run = useMutation({
    mutationFn: async () => {
      if (!task || !guide) throw new Error("请选择 Episode 和标注规范");
      const target =
        !task.assignee_id && !rights.has("annotation_task.assign")
          ? await claimRuntimeAnnotationTask(scope, task)
          : task;
      return modelRequest<RunResult>(
        scope,
        "/tasks/" + encodeURIComponent(target.task_id) + "/run",
        {
          method: "POST",
          ifMatch: target.etag,
          body: {
            guide: guide.name,
            guide_revision: guide.revision,
            expected_revision: target.current_revision,
            mutation_id: crypto.randomUUID(),
          },
        },
      );
    },
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: listKey }),
        client.invalidateQueries({ queryKey: taskKey }),
        ...(taskId
          ? [
              client.invalidateQueries({
                queryKey: taskQueryKey(
                  scope,
                  taskId,
                  "annotation",
                  session,
                ).slice(0, 6),
              }),
            ]
          : []),
      ]);
    },
  });
  const assignedElsewhere =
    task?.assignee_id &&
    task.assignee_id !== actor &&
    !rights.has("annotation_task.assign");
  const canClaim =
    task?.assignee_id ||
    rights.has("annotation_task.assign") ||
    (task?.status === "DRAFT" && rights.has("annotation_task.claim"));
  const beginEdit = (draft: Guide) => {
    saveGuide.reset();
    setEdit({ ...draft });
  };
  return (
    <Space
      orientation="vertical"
      size="middle"
      style={{ width: "100%", maxWidth: 920 }}
    >
      {list.error && <Alert type="error" title={list.error.message} />}
      <Card title="选择标注数据">
        <Form layout="vertical" disabled={run.isPending}>
          <Form.Item label="采集任务" htmlFor="model-task">
            <Select
              id="model-task"
              showSearch
              optionFilterProp="label"
              value={collectionId}
              loading={list.isFetching}
              placeholder="选择采集任务"
              options={Array.from(collectionTasks, ([value, label]) => ({
                value,
                label,
              }))}
              onChange={(value) => {
                setCollectionId(value);
                setTaskId(undefined);
                run.reset();
              }}
            />
          </Form.Item>
          <Form.Item label="Episode" htmlFor="model-episode">
            <Select
              id="model-episode"
              showSearch
              optionFilterProp="label"
              value={taskId}
              disabled={!collectionId || run.isPending}
              placeholder="选择待标注的 Episode"
              options={items
                .filter((t) => t.collection_task_id === collectionId)
                .map((t) => ({
                  value: t.task_id,
                  label:
                    t.display_label ??
                    t.data_ref?.identity.episode_id ??
                    t.rollout_id,
                }))}
              onChange={(value) => {
                setTaskId(value);
                run.reset();
              }}
            />
          </Form.Item>
          {task && (
            <div aria-label="任务可用标签">
              {task.allowed_tags?.length ? (
                task.allowed_tags.map((name) => <Tag key={name}>{name}</Tag>)
              ) : (
                <Alert type="warning" title="该采集任务尚未配置可用标签" />
              )}
            </div>
          )}
          {assignedElsewhere && (
            <Alert type="info" title="该 Episode 已由其他标注员领取" />
          )}
          {detail.error && <Alert type="error" title={detail.error.message} />}
        </Form>
      </Card>
      <Card title="标注规范与提示词">
        <Form layout="vertical" disabled={run.isPending}>
          <Form.Item label="项目标注规范" htmlFor="model-guide">
            <Select
              id="model-guide"
              showSearch
              optionFilterProp="label"
              value={guideName}
              placeholder="选择已保存的规范"
              options={guides.data?.map((g) => ({
                value: g.name,
                label: g.name,
              }))}
              onChange={(value) => {
                setGuideName(value);
                run.reset();
              }}
            />
          </Form.Item>
          {guide && (
            <>
              <Form.Item label="规范内容">
                <div style={{ whiteSpace: "pre-wrap" }}>
                  {guide.instructions}
                </div>
              </Form.Item>
              <Form.Item label="标注提示词">
                <div style={{ whiteSpace: "pre-wrap" }}>{guide.prompt}</div>
              </Form.Item>
            </>
          )}
          <Space>
            <Button onClick={() => beginEdit(newGuide)}>新建标注规范</Button>
            {guide && (
              <Button onClick={() => beginEdit(guide)}>编辑规范和提示词</Button>
            )}
          </Space>
        </Form>
        {guides.error && <Alert type="error" title={guides.error.message} />}
      </Card>
      <Button
        type="primary"
        loading={run.isPending}
        disabled={
          !task?.allowed_tags?.length ||
          !guide ||
          !!assignedElsewhere ||
          !canClaim
        }
        onClick={() => run.mutate()}
      >
        开始模型预标注
      </Button>
      {run.error && <Alert type="error" title={run.error.message} />}
      {run.data && (
        <Alert
          type={run.data.added ? "success" : "info"}
          title={
            run.data.added
              ? `已新增 ${run.data.added} 个标签区间，草稿版本 ${run.data.revision}`
              : "未发现可新增的标签区间"
          }
          action={
            <Link
              to={annotationRoutes.task.build({ taskId: run.data.task_id })}
            >
              在数据标注中查看
            </Link>
          }
        />
      )}
      <Modal
        title={edit?.revision ? "编辑标注规范" : "新建标注规范"}
        open={!!edit}
        okText="保存到项目"
        cancelText="取消"
        confirmLoading={saveGuide.isPending}
        onCancel={() => {
          if (!saveGuide.isPending) setEdit(null);
        }}
        okButtonProps={{
          disabled:
            !edit?.name.trim() ||
            !edit.instructions.trim() ||
            !edit.prompt.trim(),
        }}
        onOk={() => edit && saveGuide.mutate(edit)}
      >
        {edit && (
          <Form layout="vertical" disabled={saveGuide.isPending}>
            <Form.Item label="规范名称" htmlFor="guide-name">
              <Input
                id="guide-name"
                maxLength={128}
                value={edit.name}
                disabled={edit.revision > 0}
                onChange={(e) => setEdit({ ...edit, name: e.target.value })}
              />
            </Form.Item>
            <Form.Item label="标注规范" htmlFor="guide-rules">
              <Input.TextArea
                id="guide-rules"
                rows={4}
                maxLength={12000}
                value={edit.instructions}
                placeholder="说明标签含义、动作边界和判断标准"
                onChange={(e) =>
                  setEdit({ ...edit, instructions: e.target.value })
                }
              />
            </Form.Item>
            <Form.Item label="标注提示词" htmlFor="guide-prompt">
              <Input.TextArea
                id="guide-prompt"
                rows={5}
                maxLength={12000}
                value={edit.prompt}
                onChange={(e) => setEdit({ ...edit, prompt: e.target.value })}
              />
            </Form.Item>
            {saveGuide.error && (
              <Alert type="error" title={saveGuide.error.message} />
            )}
          </Form>
        )}
      </Modal>
    </Space>
  );
}
