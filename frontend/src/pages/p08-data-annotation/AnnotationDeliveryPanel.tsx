import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import type {
  RuntimeAnnotationHistory,
  RuntimeAnnotationScope,
} from "./runtime-annotation-adapter";
import {
  authorizeAnnotationDelivery,
  commandAnnotationDelivery,
  createAnnotationDelivery,
  listAnnotationDeliveries,
  type DeliveryJob,
  type DeliveryRequest,
} from "./annotation-deliveries";
import styles from "./workbench.module.css";

const phases = {
  queued: "等待打包",
  verifying: "校验固定版本",
  projecting: "生成分段文件",
  archiving: "正在打包",
  uploading: "保存并校验归档",
  completed: "完整包已就绪",
  failed: "打包失败",
  cancelled: "已取消",
};

export function AnnotationDeliveryPanel(props: {
  scope: RuntimeAnnotationScope;
  history: RuntimeAnnotationHistory;
  canCreate: boolean;
  canDownload: boolean;
  dirty: boolean;
}) {
  const { task } = props.history;
  const [selected, setSelected] = useState(
    task.approved_revision ?? task.current_revision,
  );
  const [members, setMembers] = useState<string[]>([]);
  const [label, setLabel] = useState("任务分段");
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
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
  const approved = selected === task.approved_revision;
  const revision = props.history.revisions.find(
    (item) => item.revision === selected,
  );
  const segments = (revision?.tags ?? [])
    .filter((tag) => members.includes(tag.annotation_id))
    .toSorted(
      (a, b) =>
        a.start_step - b.start_step ||
        a.annotation_id.localeCompare(b.annotation_id),
    );
  const overlap = segments.find(
    (tag, i) => i > 0 && tag.start_step < segments[i - 1]!.end_step,
  );
  const query = useInfiniteQuery({
    queryKey: [
      "annotation-deliveries",
      props.scope.organizationId,
      props.scope.projectId,
      props.scope.regionCode,
      task.task_id,
      task.current_revision,
    ],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      listAnnotationDeliveries(props.scope, props.history, signal, pageParam),
    getNextPageParam: (page) =>
      page.length === 25 ? page.at(-1)?.job_id : undefined,
    enabled: props.canDownload,
    refetchInterval: (q) =>
      q.state.data?.pages
        .flat()
        .some((job) => ["PENDING", "RUNNING"].includes(job.status))
        ? 3000
        : false,
    retry: false,
  });
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    const signal = controller.current?.signal;
    if (!signal) return;
    setBusy(true);
    setError(null);
    try {
      await action(signal);
    } catch (err) {
      if (!signal.aborted)
        setError(err instanceof Error ? err.message : "下载任务操作失败。");
    } finally {
      if (!signal.aborted) {
        setBusy(false);
        await query.refetch();
      }
    }
  }
  async function create(signal: AbortSignal) {
    const body: DeliveryRequest = {
      revision: selected,
      allow_unreviewed: !approved && allowUnreviewed,
      track: {
        format: "hc-subtask-track/1",
        track_id: "task-segments",
        label: label.trim(),
        annotation_ids: [...members].sort(),
      },
    };
    const signature = JSON.stringify(body);
    // Keep one command key for an ambiguous network retry of the same selection.
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    await createAnnotationDelivery(
      props.scope,
      props.history,
      body,
      pending.current.key,
      signal,
    );
    signal.throwIfAborted();
  }
  async function download(job: DeliveryJob, signal: AbortSignal) {
    const grant = await authorizeAnnotationDelivery(
      props.scope,
      props.history,
      job,
      signal,
    );
    signal.throwIfAborted();
    const anchor = document.createElement("a");
    anchor.href = grant.url;
    anchor.rel = "noopener noreferrer";
    anchor.download = "";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }
  return (
    <section
      className={styles.revisionFilesPanel}
      aria-label="带标注完整数据下载"
    >
      <strong>带标注完整数据下载</strong>
      <p>
        包含数值、RGB、已采集深度视频、深度 profile、有效性掩码和完整标注 JSON。
      </p>
      <p>
        清理区间保留原帧并标记排除状态。分段编号只使用下方选定轨道，空隙为 -1。
      </p>
      {props.dirty && <p>当前未保存的修改不包含在下载中。</p>}
      <label>
        下载标注修订{" "}
        <select
          value={selected}
          disabled={busy}
          onChange={(event) => {
            setSelected(Number(event.target.value));
            setMembers([]);
            setAllowUnreviewed(false);
            setError(null);
          }}
        >
          {props.history.revisions.toReversed().map((item) => (
            <option key={item.revision} value={item.revision}>
              r{item.revision}
              {item.revision === task.approved_revision
                ? " · 当前已审核"
                : " · 未用于当前已审核交付"}
            </option>
          ))}
        </select>
      </label>
      {!approved && (
        <label>
          <input
            type="checkbox"
            checked={allowUnreviewed}
            disabled={busy}
            onChange={(event) => setAllowUnreviewed(event.target.checked)}
          />{" "}
          明确下载未审核修订 r{selected}
        </label>
      )}
      <fieldset disabled={busy}>
        <legend>指定导出分段轨道</legend>
        <label>
          轨道名称{" "}
          <input
            value={label}
            maxLength={256}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <div style={{ maxHeight: "16rem", overflow: "auto" }}>
          {(revision?.tags ?? []).map((tag) => (
            <label key={tag.annotation_id} style={{ display: "block" }}>
              <input
                type="checkbox"
                checked={members.includes(tag.annotation_id)}
                onChange={(event) =>
                  setMembers((old) =>
                    event.target.checked
                      ? [...old, tag.annotation_id]
                      : old.filter((id) => id !== tag.annotation_id),
                  )
                }
              />
              {tag.label ?? tag.annotation_id} · 帧 [{tag.start_step},{" "}
              {tag.end_step})
            </label>
          ))}
        </div>
        <small>选中 {members.length} 段；其余标签仍完整保留在 JSON 中。</small>
      </fieldset>
      {overlap && (
        <p role="alert">
          所选轨道包含重叠区间（{overlap.label ?? overlap.annotation_id}
          ），请调整选择。
        </p>
      )}
      {!props.canCreate && <small>当前账号没有创建下载任务的权限。</small>}
      <button
        type="button"
        disabled={
          busy ||
          !props.canCreate ||
          !props.canDownload ||
          !members.length ||
          !label.trim() ||
          !!overlap ||
          (!approved && !allowUnreviewed)
        }
        onClick={() => void run(create)}
      >
        生成带标注完整包
      </button>
      {(error || query.error) && (
        <p role="alert">{error ?? query.error?.message}</p>
      )}
      <button
        type="button"
        disabled={busy || query.isFetching || !props.canDownload}
        onClick={() => void query.refetch()}
      >
        刷新打包状态
      </button>
      {query.isPending && props.canDownload && (
        <p role="status">读取打包任务…</p>
      )}
      {query.data?.pages.flat().map((job) => (
        <article
          key={job.job_id}
          aria-label={`修订 r${job.plan.binding.revision} 下载任务`}
        >
          <p>
            数据版本 v{job.plan.binding.data.version} · 标注 r
            {job.plan.binding.revision} ·
            {job.plan.review.status === "APPROVED" ? "冻结时已审核" : "未审核"}{" "}
            ·{job.plan.binding.track.label}（
            {job.plan.binding.track.annotation_ids.length} 段）
          </p>
          <p role="status">
            {phases[job.phase]}
            {job.size_bytes
              ? ` · ${(job.size_bytes / 1024 ** 2).toFixed(2)} MiB`
              : ""}
          </p>
          {job.error_code && (
            <p role="alert">
              {job.error_code}；重试会使用相同数据版本和标注修订。
            </p>
          )}
          {job.status === "READY" && (
            <button
              type="button"
              disabled={busy || !props.canDownload}
              onClick={() => void run((signal) => download(job, signal))}
            >
              下载完整包（含已采集深度）
            </button>
          )}
          {["PENDING", "RUNNING"].includes(job.status) && (
            <button
              type="button"
              disabled={busy || !props.canCreate}
              onClick={() =>
                void run(async (signal) => {
                  await commandAnnotationDelivery(
                    props.scope,
                    props.history,
                    job,
                    "cancel",
                    signal,
                  );
                })
              }
            >
              取消打包
            </button>
          )}
          {["FAILED", "CANCELLED"].includes(job.status) && (
            <button
              type="button"
              disabled={busy || !props.canCreate}
              onClick={() =>
                void run(async (signal) => {
                  await commandAnnotationDelivery(
                    props.scope,
                    props.history,
                    job,
                    "retry",
                    signal,
                  );
                })
              }
            >
              重试同一版本
            </button>
          )}
        </article>
      ))}
      {query.hasNextPage && (
        <button
          type="button"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          更多下载任务
        </button>
      )}
    </section>
  );
}
