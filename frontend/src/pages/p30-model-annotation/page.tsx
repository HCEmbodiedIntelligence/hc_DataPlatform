import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Progress,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Tag,
} from "antd";
import { useEffect, useRef, useState } from "react";
import { BookOpenText, ListChecks, Play, X } from "lucide-react";
import { EpisodePicker, type Episode } from "./EpisodePicker";
import styles from "./styles.module.css";
import { Link } from "react-router-dom";
import { annotationRoutes } from "../../features/annotation/routing";
import {
  claimRuntimeAnnotationTask,
  getRuntimeAnnotationTask,
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
  const episodesKey = ["model-annotation-episodes", session, scope];
  const list = useQuery({
    queryKey: episodesKey,
    queryFn: ({ signal }) =>
      modelRequest<Episode[]>(scope, "/episodes", { method: "GET", signal }),
  });
  const guideKey = ["model-annotation-guides", session, scope];
  const guides = useQuery({
    queryKey: guideKey,
    queryFn: ({ signal }) =>
      modelRequest<Guide[]>(scope, "/guides", { method: "GET", signal }),
  });
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [guideName, setGuideName] = useState<string>();
  const [edit, setEdit] = useState<Guide | null>(null);
  const [results, setResults] = useState<
    Array<{ id: string; label: string; saved?: RunResult; error?: string }>
  >([]);
  const [total, setTotal] = useState(0);
  const [active, setActive] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  function blocked(
    task: Pick<
      RuntimeAnnotationTask,
      "allowed_tags" | "status" | "assignee_id"
    >,
  ) {
    if (!["DRAFT", "NEEDS_REVISION"].includes(task.status))
      return "已不在待标注队列";
    if (!rights.has("annotation_task.assign")) {
      if (task.assignee_id && task.assignee_id !== actor)
        return "其他标注员已领取";
      if (
        !task.assignee_id &&
        (task.status !== "DRAFT" || !rights.has("annotation_task.claim"))
      )
        return "暂无领取权限";
    }
  }
  const items = list.data ?? [];
  const selectedKeys = new Set(selectedIds);
  const selected = items.filter(
    (item) => selectedKeys.has(item.task_id) && !blocked(item),
  );
  const missingTags = selected.filter((item) => !item.allowed_tags.length);
  const tags = [...new Set(selected.flatMap((item) => item.allowed_tags))];
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
      if (
        !guide ||
        !selected.length ||
        missingTags.length ||
        controller.current
      )
        return;
      const work = new AbortController();
      controller.current = work;
      setTotal(selected.length);
      setResults([]);
      try {
        for (const episode of selected) {
          if (work.signal.aborted) break;
          setActive(episode.label);
          try {
            const task = await getRuntimeAnnotationTask(
              scope,
              episode.task_id,
              work.signal,
            );
            const reason = blocked(task);
            if (reason) throw new Error(reason);
            if (!task.allowed_tags?.length)
              throw new Error("采集方案尚未配置可用标签");
            const target =
              !task.assignee_id && !rights.has("annotation_task.assign")
                ? await claimRuntimeAnnotationTask(scope, task)
                : task;
            work.signal.throwIfAborted();
            const saved = await modelRequest<RunResult>(
              scope,
              "/tasks/" + encodeURIComponent(task.task_id) + "/run",
              {
                method: "POST",
                ifMatch: target.etag,
                signal: work.signal,
                body: {
                  guide: guide.name,
                  guide_revision: guide.revision,
                  expected_revision: target.current_revision,
                  mutation_id: crypto.randomUUID(),
                },
              },
            );
            setResults((rows) => [
              ...rows,
              { id: task.task_id, label: episode.label, saved },
            ]);
            setSelectedIds((ids) => ids.filter((id) => id !== task.task_id));
          } catch (error) {
            if (work.signal.aborted) break;
            setResults((rows) => [
              ...rows,
              {
                id: episode.task_id,
                label: episode.label,
                error: error instanceof Error ? error.message : "预标注失败",
              },
            ]);
          } finally {
            void client.invalidateQueries({
              queryKey: taskQueryKey(
                scope,
                episode.task_id,
                "annotation",
                session,
              ).slice(0, 6),
            });
          }
        }
      } finally {
        controller.current = null;
        setActive("");
        await Promise.all([
          client.invalidateQueries({ queryKey: episodesKey }),
          client.invalidateQueries({ queryKey: listKey }),
        ]);
      }
    },
    retry: false,
  });
  const beginEdit = (draft: Guide) => {
    saveGuide.reset();
    setEdit({ ...draft });
  };
  const failed = results.filter((row) => row.error).length;
  return (
    <div className={styles.workspace}>
      <div className={styles.main}>
        {list.error && (
          <Alert
            type="error"
            showIcon
            title={list.error.message}
            action={<Button onClick={() => void list.refetch()}>重试</Button>}
          />
        )}
        <EpisodePicker
          items={items}
          selected={selected.map((item) => item.task_id)}
          setSelected={setSelectedIds}
          blocked={blocked}
          busy={run.isPending}
          loading={list.isLoading}
        />
      </div>
      <aside className={styles.sidebar} aria-label="预标注设置">
        <div className={styles.sectionHead}>
          <h2>
            <BookOpenText size={20} aria-hidden="true" />
            标注规范
          </h2>
        </div>
        <Form layout="vertical" disabled={run.isPending}>
          <Form.Item label="项目标注规范" htmlFor="model-guide">
            <Select
              id="model-guide"
              showSearch
              optionFilterProp="label"
              value={guideName}
              loading={guides.isLoading}
              placeholder="选择已保存的规范"
              options={guides.data?.map((g) => ({
                value: g.name,
                label: g.name,
              }))}
              onChange={setGuideName}
            />
          </Form.Item>
          {guide && (
            <div className={styles.guide}>
              <h3>规范内容</h3>
              <p>{guide.instructions}</p>
              <h3>标注提示词</h3>
              <p>{guide.prompt}</p>
            </div>
          )}
          <Space wrap>
            <Button onClick={() => beginEdit(newGuide)}>新建标注规范</Button>
            {guide && (
              <Button type="link" onClick={() => beginEdit(guide)}>
                编辑规范
              </Button>
            )}
          </Space>
        </Form>
        {guides.error && <Alert type="error" title={guides.error.message} />}
        <section className={styles.summary}>
          <div className={styles.sectionHead}>
            <h2>
              <ListChecks size={20} aria-hidden="true" />
              本次预标注
            </h2>
            {!!selected.length && (
              <Button
                type="link"
                size="small"
                disabled={run.isPending}
                onClick={() => setSelectedIds([])}
              >
                清空选择
              </Button>
            )}
          </div>
          <div className={styles.quantity}>
            <span>已选 Episode</span>
            <strong>{selected.length}</strong>
          </div>
          {!!selected.length && (
            <details className={styles.selection}>
              <summary>查看所选 Episode</summary>
              <ul>
                {selected.map((item) => (
                  <li key={item.task_id}>
                    <span>{item.label}</span>
                    <Button
                      size="small"
                      type="text"
                      aria-label={`移除 ${item.label}`}
                      disabled={run.isPending}
                      icon={<X size={14} />}
                      onClick={() =>
                        setSelectedIds((ids) =>
                          ids.filter((id) => id !== item.task_id),
                        )
                      }
                    />
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!tags.length && (
            <div className={styles.tagBlock}>
              <h3>所选任务的可用标签</h3>
              {tags.map((name) => (
                <Tag key={name}>{name}</Tag>
              ))}
            </div>
          )}
          {!!missingTags.length && (
            <Alert
              type="warning"
              showIcon
              title={`所选 Episode 中有 ${missingTags.length} 项缺少可用标签`}
              description="可在对应采集方案中配置标签，或移除这些项后继续。"
              action={
                <Button
                  size="small"
                  disabled={run.isPending}
                  onClick={() =>
                    setSelectedIds(
                      selected
                        .filter((item) => item.allowed_tags.length)
                        .map((item) => item.task_id),
                    )
                  }
                >
                  移除这些项
                </Button>
              }
            />
          )}
          {!run.isPending && (!selected.length || !guide) && (
            <p className={styles.hint}>
              {!selected.length
                ? "请勾选要预标注的 Episode"
                : "请选择项目标注规范"}
            </p>
          )}
          <Button
            block
            type="primary"
            size="large"
            icon={<Play size={16} />}
            loading={run.isPending}
            disabled={
              !selected.length || !!missingTags.length || !guide || list.isError
            }
            onClick={() => run.mutate()}
          >
            开始模型预标注
          </Button>
        </section>
        {(run.isPending || results.length > 0) && (
          <section className={styles.results} aria-label="执行结果">
            <h3 aria-live="polite">
              {run.isPending
                ? `处理进度 ${results.length} / ${total}`
                : `执行结果 · 成功 ${results.length - failed}，失败 ${failed}`}
            </h3>
            {run.isPending && (
              <>
                <Progress
                  percent={Math.round(
                    (100 * results.length) / Math.max(1, total),
                  )}
                  showInfo={false}
                />
                {active && <small>正在处理 {active}，请保持页面打开</small>}
              </>
            )}
            <ul>
              {results.map((row) => (
                <li key={row.id}>
                  <strong>{row.label}</strong>
                  {row.saved ? (
                    <>
                      <span>
                        {row.saved.added
                          ? `新增 ${row.saved.added} 个标签区间`
                          : "未发现可新增的标签区间"}
                      </span>
                      <Link
                        to={annotationRoutes.task.build({ taskId: row.id })}
                      >
                        在数据标注中查看
                      </Link>
                    </>
                  ) : (
                    <span className={styles.error}>{row.error}</span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </aside>
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
    </div>
  );
}
