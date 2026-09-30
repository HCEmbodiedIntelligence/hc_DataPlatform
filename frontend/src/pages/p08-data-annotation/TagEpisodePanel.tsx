import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { routes as datasetRoutes } from "../../features/datasets/routing";
import { annotationRoutes } from "./routes";
import {
  commandTagEpisode,
  createTagEpisode,
  listTagEpisodes,
  type TagEpisodeRequest,
} from "./tag-episodes";
import type {
  RuntimeAnnotationHistory,
  RuntimeAnnotationScope,
} from "./runtime-annotation-adapter";
import styles from "./workbench.module.css";

const phases = {
  queued: "等待创建",
  verifying: "校验父数据与标注",
  deriving: "生成区间数据",
  committing: "保存独立数据包",
  projecting: "登记 Episode",
  annotating: "创建独立标注任务",
  completed: "Episode 已创建",
  failed: "创建未完成",
  cancelled: "已取消",
};

export function TagEpisodePanel(props: {
  scope: RuntimeAnnotationScope;
  history: RuntimeAnnotationHistory;
  canCreate: boolean;
  canRead: boolean;
  dirty: boolean;
}) {
  const { task } = props.history;
  const [selected, setSelected] = useState(
    task.approved_revision ?? task.current_revision,
  );
  const [tagId, setTagId] = useState("");
  const [name, setName] = useState("");
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    return () => current.abort();
  }, [
    task.task_id,
    props.scope.organizationId,
    props.scope.projectId,
    props.scope.regionCode,
  ]);
  const revision = props.history.revisions.find((r) => r.revision === selected);
  const tag = revision?.tags.find((t) => t.annotation_id === tagId);
  const approved = selected === task.approved_revision;
  const query = useInfiniteQuery({
    queryKey: [
      "tag-episodes",
      props.scope.organizationId,
      props.scope.projectId,
      props.scope.regionCode,
      task.task_id,
      task.current_revision,
    ],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      listTagEpisodes(props.scope, props.history, signal, pageParam),
    getNextPageParam: (page) =>
      page.length === 25 ? page.at(-1)?.job_id : undefined,
    enabled: props.canRead,
    refetchInterval: (q) =>
      q.state.data?.pages
        .flat()
        .some((j) => j.status === "PENDING" || j.status === "RUNNING")
        ? 3000
        : false,
    retry: false,
  });
  async function run(action: (signal: AbortSignal) => Promise<unknown>) {
    const signal = controller.current?.signal;
    if (!signal) return;
    setBusy(true);
    setError(null);
    try {
      await action(signal);
    } catch (err) {
      if (!signal.aborted)
        setError(
          err instanceof Error ? err.message : "创建 Episode 操作失败。",
        );
    } finally {
      if (!signal.aborted) {
        setBusy(false);
        await query.refetch();
      }
    }
  }
  async function create(signal: AbortSignal) {
    if (!tag) return;
    const body: TagEpisodeRequest = {
      revision: selected,
      annotation_id: tag.annotation_id,
      allow_unreviewed: !approved && allowUnreviewed,
      ...(name.trim() ? { display_name: name.trim() } : {}),
    };
    const signature = JSON.stringify(body);
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    await createTagEpisode(
      props.scope,
      props.history,
      body,
      pending.current.key,
      signal,
    );
    signal.throwIfAborted();
  }
  return (
    <section
      className={styles.revisionFilesPanel}
      aria-label="从 Tag 创建独立 Episode"
    >
      <strong>从 Tag 创建独立 Episode</strong>
      <p>
        选定区间生成独立数据条目，包含数值、RGB、已采集深度视频、profile
        和有效性掩码。原始数据和 Tag 保留。
      </p>
      <p>
        新条目的自动质检、人工数据审核和标注审核分别处理，不继承父条目的通过状态。
      </p>
      {props.dirty && (
        <p>当前未保存的修改不包含在新 Episode 中；请先保存需要使用的 Tag。</p>
      )}
      <label>
        来源标注修订{" "}
        <select
          value={selected}
          disabled={busy}
          onChange={(event) => {
            setSelected(Number(event.target.value));
            setTagId("");
            setAllowUnreviewed(false);
            setError(null);
          }}
        >
          {props.history.revisions.toReversed().map((r) => (
            <option key={r.revision} value={r.revision}>
              r{r.revision}
              {r.revision === task.approved_revision
                ? " · 当前已审核"
                : " · 未用于当前已审核交付"}
            </option>
          ))}
        </select>
      </label>
      <label>
        来源 Tag{" "}
        <select
          value={tagId}
          disabled={busy}
          onChange={(event) => setTagId(event.target.value)}
        >
          <option value="">请选择一个已保存的 Tag</option>
          {(revision?.tags ?? []).map((t) => (
            <option key={t.annotation_id} value={t.annotation_id}>
              {t.label ?? t.annotation_id} · 帧 [{t.start_step}, {t.end_step})
            </option>
          ))}
        </select>
      </label>
      {tag && (
        <p>
          来源范围：帧 [{tag.start_step}, {tag.end_step})，共{" "}
          {tag.end_step - tag.start_step} 帧。
        </p>
      )}
      <label>
        新 Episode 名称（可选）{" "}
        <input
          value={name}
          maxLength={256}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      {!approved && (
        <label>
          <input
            type="checkbox"
            checked={allowUnreviewed}
            disabled={busy}
            onChange={(event) => setAllowUnreviewed(event.target.checked)}
          />
          明确使用未审核修订 r{selected} 创建新 Episode
        </label>
      )}
      {!props.canRead && (
        <p>当前账号缺少读取父数据、标注或下载源文件的权限。</p>
      )}
      {!props.canCreate && <p>当前账号没有创建新数据的权限。</p>}
      <button
        type="button"
        disabled={
          busy ||
          !props.canRead ||
          !props.canCreate ||
          !tag ||
          (!approved && !allowUnreviewed)
        }
        onClick={() => void run(create)}
      >
        创建独立 Episode
      </button>
      {error && <p role="alert">{error}</p>}
      {props.canRead && query.isPending && <p role="status">读取创建任务…</p>}
      {props.canRead && query.error && (
        <p role="alert">
          {query.error.message}
          <button type="button" onClick={() => void query.refetch()}>
            重新读取任务
          </button>
        </p>
      )}
      {props.canRead &&
        query.data?.pages.flat().map((job) => (
          <article key={job.job_id}>
            <p>
              {job.selection.display_name} · r{job.selection.revision} · 帧 [
              {job.selection.start_step}, {job.selection.end_step}) ·{" "}
              {phases[job.phase]}
            </p>
            {job.selection.review_status === "UNREVIEWED" && (
              <small>创建时使用了未审核标注修订。</small>
            )}
            {job.error_code && <p role="alert">{job.error_code}</p>}
            {job.status === "FAILED" && job.committed_data && (
              <p>
                独立数据包已保存，后续发布未完成。重试将继续发布同一数据版本。
              </p>
            )}
            {job.viewer_target && (
              <p>
                <a
                  target="_blank"
                  rel="noopener noreferrer"
                  href={datasetRoutes.episodeViewer.build({
                    datasetId: job.viewer_target.dataset_id,
                    versionId: job.viewer_target.version_id,
                    episodeId: job.viewer_target.episode_id,
                  })}
                >
                  查看新 Episode（新窗口）
                </a>
              </p>
            )}
            {job.annotation_task_id && (
              <p>
                <a
                  target="_blank"
                  rel="noopener noreferrer"
                  href={annotationRoutes.task.build({
                    taskId: job.annotation_task_id,
                  })}
                >
                  打开新标注任务（新窗口）
                </a>
              </p>
            )}
            {(job.status === "FAILED" || job.status === "CANCELLED") && (
              <button
                type="button"
                disabled={busy || !props.canCreate}
                onClick={() =>
                  void run((signal) =>
                    commandTagEpisode(
                      props.scope,
                      props.history,
                      job,
                      "retry",
                      signal,
                    ),
                  )
                }
              >
                重试创建同一 Episode
              </button>
            )}
            {job.status === "PENDING" && job.attempts === 0 && (
              <button
                type="button"
                disabled={busy || !props.canCreate}
                onClick={() =>
                  void run((signal) =>
                    commandTagEpisode(
                      props.scope,
                      props.history,
                      job,
                      "cancel",
                      signal,
                    ),
                  )
                }
              >
                取消尚未开始的创建
              </button>
            )}
          </article>
        ))}
      {props.canRead && query.hasNextPage && (
        <button
          type="button"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          读取更多创建任务
        </button>
      )}
    </section>
  );
}
