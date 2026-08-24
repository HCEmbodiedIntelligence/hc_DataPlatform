import { useState } from "react";
import type { JSX } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CircleAlert,
  RefreshCw,
  Square,
  WandSparkles,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { DangerousActionDialog } from "../../features/annotation";
import { isDomainError } from "../../shared/api/domain-error";
import {
  applyRuntimeAutoAnnotationJob,
  cancelRuntimeAutoAnnotationJob,
  createRuntimeAutoAnnotationJob,
  getRuntimeAutoAnnotationJob,
  loadRuntimeAutoAnnotationCapability,
  retryRuntimeAutoAnnotationJob,
} from "./runtime-annotation-adapter";
import type {
  RuntimeAnnotationScope,
  RuntimeAnnotationTask,
  RuntimeAutoAnnotationJob,
} from "./runtime-annotation-adapter";
import styles from "./workbench.module.css";

type PendingAction = "start" | "cancel" | "retry" | "apply" | null;

const JOB_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

function capabilityKey(scope: RuntimeAnnotationScope) {
  return [
    "p08-auto-annotation-capability",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
  ] as const;
}

function jobKey(scope: RuntimeAnnotationScope, taskId: string, jobId: string) {
  return [
    "p08-auto-annotation-job",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    taskId,
    jobId,
  ] as const;
}

function running(job: RuntimeAutoAnnotationJob | undefined): boolean {
  return job?.status === "QUEUED" || job?.status === "RUNNING";
}

function jobStatusLabel(status: RuntimeAutoAnnotationJob["status"]): string {
  const labels: Readonly<Record<RuntimeAutoAnnotationJob["status"], string>> = {
    QUEUED: "等待 Worker",
    RUNNING: "运行中",
    SUCCEEDED: "结果待确认",
    FAILED: "执行失败",
    CANCELLED: "已取消",
    APPLIED: "已追加为修订",
  };
  return labels[status];
}

function errorText(error: unknown): string | null {
  if (!error) return null;
  if (isDomainError(error)) {
    return `${error.message}${error.problemCode ? `（${error.problemCode}）` : ""}${error.requestId ? `（请求 ${error.requestId}）` : ""}`;
  }
  return error instanceof Error ? error.message : "自动标注操作未完成。";
}

export function RuntimeAutoAnnotationPanel(props: {
  readonly scope: RuntimeAnnotationScope;
  readonly task: RuntimeAnnotationTask;
  readonly canUse: boolean;
  readonly dirty: boolean;
  readonly onApplied: () => Promise<void>;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawJobId = searchParams.get("autoJobId");
  const jobId = rawJobId && JOB_ID_PATTERN.test(rawJobId) ? rawJobId : null;
  const [providerValue, setProviderValue] = useState("");
  const [modelValue, setModelValue] = useState("");
  const [startStep, setStartStep] = useState("0");
  const [endStep, setEndStep] = useState(
    String(Math.max(1, props.task.base_step_count ?? 1)),
  );
  const [pending, setPending] = useState<PendingAction>(null);
  const [operationError, setOperationError] = useState<unknown>(null);
  const [confirmApply, setConfirmApply] = useState(false);

  const capabilityQuery = useQuery({
    queryKey: capabilityKey(props.scope),
    queryFn: ({ signal }) =>
      loadRuntimeAutoAnnotationCapability(props.scope, signal),
    staleTime: 60_000,
    retry: false,
  });
  const jobQuery = useQuery({
    queryKey: jobKey(props.scope, props.task.task_id, jobId ?? "disabled"),
    queryFn: ({ signal }) =>
      getRuntimeAutoAnnotationJob(
        props.scope,
        props.task.task_id,
        jobId!,
        signal,
      ),
    enabled: jobId !== null,
    retry: false,
    refetchInterval: (query) => (running(query.state.data) ? 1_000 : false),
  });
  const capability = capabilityQuery.data;
  const provider =
    capability?.providers.find((item) => item.provider === providerValue) ??
    capability?.providers[0];
  const model =
    provider?.models.find((item) => item === modelValue) ?? provider?.models[0];
  const job = jobQuery.data;
  const minimum = Number(startStep);
  const maximum = Number(endStep);
  const selectionValid =
    Number.isSafeInteger(minimum) &&
    Number.isSafeInteger(maximum) &&
    minimum >= 0 &&
    maximum > minimum &&
    maximum <= (props.task.base_step_count ?? 0);
  const visibleError = errorText(
    operationError ?? capabilityQuery.error ?? jobQuery.error,
  );

  const setJob = (next: RuntimeAutoAnnotationJob) => {
    queryClient.setQueryData(
      jobKey(props.scope, props.task.task_id, next.job_id),
      next,
    );
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set("autoJobId", next.job_id);
    setSearchParams(nextParams, { replace: true });
  };
  const invoke = async (
    action: Exclude<PendingAction, null>,
    operation: () => Promise<void>,
  ) => {
    if (pending !== null) return;
    setPending(action);
    setOperationError(null);
    try {
      await operation();
    } catch (error) {
      setOperationError(error);
    } finally {
      setPending(null);
    }
  };
  const start = () =>
    invoke("start", async () => {
      if (!provider || !model || !selectionValid) return;
      setJob(
        await createRuntimeAutoAnnotationJob(props.scope, props.task, {
          provider: provider.provider,
          model,
          startStep: minimum,
          endStep: maximum,
        }),
      );
    });
  const cancel = () =>
    invoke("cancel", async () => {
      if (!job) return;
      setJob(
        await cancelRuntimeAutoAnnotationJob(
          props.scope,
          props.task.task_id,
          job.job_id,
        ),
      );
    });
  const retry = () =>
    invoke("retry", async () => {
      if (!job) return;
      setJob(
        await retryRuntimeAutoAnnotationJob(
          props.scope,
          props.task.task_id,
          job.job_id,
        ),
      );
    });
  const apply = () =>
    invoke("apply", async () => {
      if (!job) return;
      await applyRuntimeAutoAnnotationJob(props.scope, props.task, job);
      setJob(
        await getRuntimeAutoAnnotationJob(
          props.scope,
          props.task.task_id,
          job.job_id,
        ),
      );
      await props.onApplied();
    });

  return (
    <section className={styles.autoAnnotationPanel} aria-label="自动标注任务">
      <header>
        <span>
          <Bot aria-hidden="true" size={15} />
          <strong>自动标注</strong>
        </span>
        {job ? (
          <em data-status={job.status}>{jobStatusLabel(job.status)}</em>
        ) : null}
      </header>

      {capabilityQuery.isLoading ? (
        <p>正在读取 Provider 与配额配置…</p>
      ) : capability && !capability.enabled ? (
        <p>当前环境没有配置自动标注 Provider；不会创建假任务或假结果。</p>
      ) : capability ? (
        <div className={styles.autoAnnotationFields}>
          <label htmlFor="auto-annotation-provider">
            <span>Provider</span>
            <select
              disabled={pending !== null || running(job)}
              id="auto-annotation-provider"
              value={provider?.provider ?? ""}
              onChange={(event) => {
                setProviderValue(event.target.value);
                setModelValue("");
              }}
            >
              {capability.providers.map((item) => (
                <option key={item.provider} value={item.provider}>
                  {item.provider}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="auto-annotation-model">
            <span>模型</span>
            <select
              disabled={pending !== null || running(job)}
              id="auto-annotation-model"
              value={model ?? ""}
              onChange={(event) => setModelValue(event.target.value)}
            >
              {(provider?.models ?? []).map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="auto-annotation-start-step">
            <span>开始步</span>
            <input
              disabled={pending !== null || running(job)}
              id="auto-annotation-start-step"
              min={0}
              type="number"
              value={startStep}
              onChange={(event) => setStartStep(event.target.value)}
            />
          </label>
          <label htmlFor="auto-annotation-end-step">
            <span>结束步（开区间）</span>
            <input
              disabled={pending !== null || running(job)}
              id="auto-annotation-end-step"
              max={props.task.base_step_count ?? undefined}
              min={1}
              type="number"
              value={endStep}
              onChange={(event) => setEndStep(event.target.value)}
            />
          </label>
        </div>
      ) : null}

      {job ? (
        <dl className={styles.autoAnnotationFacts}>
          <div>
            <dt>进度</dt>
            <dd>{job.progress_percent}%</dd>
          </div>
          <div>
            <dt>预计成本</dt>
            <dd>{job.estimated_cost_micros.toLocaleString("zh-CN")} μ</dd>
          </div>
          <div>
            <dt>固定来源</dt>
            <dd>r{job.source_revision}</dd>
          </div>
          {job.tags && job.operations ? (
            <div>
              <dt>结果</dt>
              <dd>
                {job.tags.length} Tag / {job.operations.length} 操作
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {rawJobId && !jobId ? (
        <p className={styles.autoAnnotationError} role="alert">
          <CircleAlert aria-hidden="true" size={14} />
          URL 中的自动标注任务 ID 非法，未向服务端发送请求。
        </p>
      ) : null}
      {visibleError ? (
        <p className={styles.autoAnnotationError} role="alert">
          <CircleAlert aria-hidden="true" size={14} />
          {visibleError}
        </p>
      ) : null}
      {!selectionValid && capability?.enabled ? (
        <small>步区间必须位于当前不可变 Lance 快照内。</small>
      ) : null}
      {props.dirty ? (
        <small>先保存本地 Tag 修改，再启动或应用任务。</small>
      ) : null}

      <div className={styles.autoAnnotationActions}>
        {capabilityQuery.error ? (
          <button type="button" onClick={() => void capabilityQuery.refetch()}>
            <RefreshCw aria-hidden="true" size={14} />
            重试配置
          </button>
        ) : null}
        {capability?.enabled && (!job || job.status === "APPLIED") ? (
          <button
            disabled={
              !props.canUse ||
              props.dirty ||
              !selectionValid ||
              pending !== null
            }
            type="button"
            onClick={() => void start()}
          >
            <WandSparkles aria-hidden="true" size={14} />
            {pending === "start" ? "创建中…" : "启动自动标注"}
          </button>
        ) : null}
        {running(job) ? (
          <button
            disabled={!props.canUse || pending !== null}
            type="button"
            onClick={() => void cancel()}
          >
            <Square aria-hidden="true" size={13} />
            {pending === "cancel" ? "取消中…" : "取消任务"}
          </button>
        ) : null}
        {job?.status === "FAILED" || job?.status === "CANCELLED" ? (
          <button
            disabled={!props.canUse || props.dirty || pending !== null}
            type="button"
            onClick={() => void retry()}
          >
            <RefreshCw aria-hidden="true" size={14} />
            {pending === "retry" ? "重试中…" : "重试同一任务"}
          </button>
        ) : null}
        {job?.status === "SUCCEEDED" ? (
          <button
            disabled={!props.canUse || props.dirty || pending !== null}
            type="button"
            onClick={() => setConfirmApply(true)}
          >
            应用为新修订
          </button>
        ) : null}
      </div>

      <DangerousActionDialog
        confirmLabel="确认应用自动标注"
        impact={[
          "Provider 结果将追加为新的不可变标注修订",
          "来源修订、Provider、模型、成本与任务审计继续保留",
        ]}
        open={confirmApply}
        pending={pending === "apply"}
        stableResourceId={job?.job_id ?? props.task.task_id}
        title="应用自动标注结果"
        onCancel={() => setConfirmApply(false)}
        onConfirm={() => {
          setConfirmApply(false);
          void apply();
        }}
      >
        <p>仅应用当前固定来源 r{job?.source_revision ?? "?"} 的结果。</p>
      </DangerousActionDialog>
    </section>
  );
}
