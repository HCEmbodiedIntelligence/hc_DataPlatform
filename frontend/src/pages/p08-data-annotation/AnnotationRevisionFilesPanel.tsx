import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  RuntimeAnnotationHistory,
  RuntimeAnnotationScope,
} from "../../features/annotation/runtime/runtime-annotation-adapter";
import {
  downloadRevisionFile,
  loadRevisionFile,
  requestRevisionFile,
} from "./revision-files";
import styles from "./workbench.module.css";

const labels = {
  NOT_REQUESTED: "尚未生成",
  PENDING: "等待生成",
  GENERATING: "正在生成",
  READY: "文件已就绪",
  FAILED: "生成失败",
} as const;

export function AnnotationRevisionFilesPanel(props: {
  scope: RuntimeAnnotationScope;
  history: RuntimeAnnotationHistory;
  canRetry: boolean;
  canDownload: boolean;
  dirty: boolean;
}) {
  const { task } = props.history;
  const initial = task.approved_revision ?? task.current_revision;
  const [selected, setSelected] = useState(initial);
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const downloadController = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    downloadController.current = controller;
    return () => controller.abort();
  }, [
    props.scope.organizationId,
    props.scope.projectId,
    props.scope.regionCode,
    task.task_id,
    selected,
  ]);
  useEffect(() => {
    setSelected(initial);
    setAllowUnreviewed(false);
    setError(null);
  }, [initial, task.task_id]);
  const revision = props.history.revisions.find(
    (item) => item.revision === selected,
  )!;
  const approved = selected === task.approved_revision;
  const query = useQuery({
    queryKey: [
      "annotation-revision-file",
      props.scope.organizationId,
      props.scope.projectId,
      props.scope.regionCode,
      task.task_id,
      selected,
      revision.content_hash,
    ],
    queryFn: ({ signal }) => loadRevisionFile(props.scope, revision, signal),
    refetchInterval: (q) =>
      ["PENDING", "GENERATING"].includes(q.state.data?.status ?? "")
        ? 3000
        : false,
    retry: false,
  });
  const file = query.data;
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "标注文件操作失败。");
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    if (!file) return;
    const signal = downloadController.current?.signal;
    const blob = await downloadRevisionFile(
      props.scope,
      revision,
      file,
      !approved && allowUnreviewed,
      signal,
    );
    signal?.throwIfAborted();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `annotation-r${selected}-${approved ? "approved" : "unreviewed"}.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    // Keep the URL alive while the browser starts the local download.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className={styles.revisionFilesPanel} aria-label="版本化标注 JSON">
      <p>修订 r{task.current_revision} 已保存。文件生成状态单独显示。</p>
      {props.dirty && <p>当前未保存的修改不包含在文件中。</p>}
      <label>
        选择文件修订
        <select
          value={selected}
          disabled={busy}
          onChange={(event) => {
            setSelected(Number(event.target.value));
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
      <p role="status">
        {file
          ? labels[file.status]
          : query.isPending
            ? "正在读取文件状态…"
            : "文件状态不可用"}
      </p>
      {file?.error_code && (
        <p role="alert">生成失败（{file.error_code}），可重试生成同一修订。</p>
      )}
      {(error || query.error) && (
        <p role="alert">{error ?? query.error?.message}</p>
      )}
      {!approved && (
        <label>
          <input
            type="checkbox"
            checked={allowUnreviewed}
            disabled={busy}
            onChange={(event) => setAllowUnreviewed(event.target.checked)}
          />
          明确下载未审核修订 r{selected}
        </label>
      )}
      <button
        type="button"
        disabled={
          busy ||
          !props.canDownload ||
          file?.status !== "READY" ||
          (!approved && !allowUnreviewed)
        }
        onClick={() => void run(download)}
      >
        下载{approved ? "已审核" : "未审核"} r{selected} JSON
      </button>
      {!props.canDownload && <small>当前账号没有下载权限。</small>}
      <button
        type="button"
        disabled={
          busy ||
          !props.canRetry ||
          !file ||
          ["PENDING", "GENERATING"].includes(file.status)
        }
        onClick={() =>
          void run(async () => {
            try {
              await requestRevisionFile(
                props.scope,
                revision,
                file!.generation,
              );
            } finally {
              await query.refetch();
            }
          })
        }
      >
        {file?.status === "FAILED"
          ? "重试生成"
          : file?.status === "READY"
            ? "重新生成文件"
            : "生成文件"}
      </button>
      {!props.canRetry && <small>当前账号没有生成或重试权限。</small>}
      <button
        type="button"
        disabled={busy || query.isFetching}
        onClick={() => void query.refetch()}
      >
        刷新文件状态
      </button>
      <small>
        JSON 包含固定数据版本、完整标签、Schema
        和区间约定。审核记录独立绑定，后续审核不会修改该文件。
      </small>
    </section>
  );
}
