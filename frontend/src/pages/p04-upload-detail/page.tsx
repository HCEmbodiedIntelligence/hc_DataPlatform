import { Alert, Button, Space, Tag, Typography } from "antd";
import {
  ArrowLeft,
  Boxes,
  CircleGauge,
  Clock3,
  Database,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  useRetryUploadVerification,
  useUploadBootstrap,
  useUploadEvents,
  useUploadObjects,
  useVerificationRuns,
} from "../../features/ingest/api";
import { createMutationIntentKey } from "../../features/ingest/mutation-machine";
import { routes } from "../../features/ingest/routing";
import { ingestUploadAuthorizationVault } from "../../features/ingest/upload/authorization-vault";
import { useIngestScope } from "../../features/ingest/use-ingest-scope";
import {
  canRequestQuarantineRelease,
  validatePipeline,
} from "../../features/ingest/validation-pipeline";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useAsyncJob } from "../../shared/jobs/use-async-job";
import {
  DataCursorPager,
  PageState,
  StandardPageScaffold,
  StatusTag,
  type DangerConflict,
  type DangerPreflightEvidence,
  type PageStateKind,
} from "../../shared/ui";
import { DangerousUploadActionDialog } from "./components/DangerousUploadActionDialog";
import { QuarantinePanel } from "./components/QuarantinePanel";
import { UploadEventTimeline } from "./components/UploadEventTimeline";
import { UploadManifestSummary } from "./components/UploadManifestSummary";
import { UploadObjectsTable } from "./components/UploadObjectsTable";
import { ValidationPipeline } from "./components/ValidationPipeline";
import {
  updateUploadDetailSearch,
  uploadDetailQueryCodec,
} from "./query-codec";
import styles from "./styles.module.css";

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  switch (error.code) {
    case "FORBIDDEN":
    case "UNAUTHENTICATED":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
    case "VERSION_CONFLICT":
    case "PRECONDITION_FAILED":
      return "conflict";
    case "RATE_LIMITED":
      return "rate-limited";
    case "NETWORK_ERROR":
      return "offline";
    case "CONTRACT_MISMATCH":
      return "contract-mismatch";
    default:
      return "error";
  }
}

function regionState(query: {
  readonly isPending: boolean;
  readonly isError: boolean;
  readonly isFetching: boolean;
  readonly error: unknown;
  readonly data?: { readonly items: readonly unknown[] };
}): PageStateKind | "ready" {
  if (query.isPending) return "loading";
  if (query.isError) return stateFromError(query.error);
  if (query.data?.items.length === 0) return "empty";
  return query.isFetching ? "refreshing" : "ready";
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function conflictFrom(error: unknown): DangerConflict | null {
  if (!isDomainError(error)) return null;
  if (error.httpStatus === 409 || error.code === "VERSION_CONFLICT")
    return { status: 409, code: error.code };
  if (error.httpStatus === 412 || error.code === "PRECONDITION_FAILED")
    return { status: 412, code: error.code };
  return null;
}

function safeOperationError(error: unknown): string | null {
  if (!error) return null;
  if (!isDomainError(error)) return "复验请求未完成；隔离事实保持不变。";
  const message =
    error.code === "VERSION_CONFLICT" || error.code === "PRECONDITION_FAILED"
      ? "资源版本已变化，请重新加载并再次预检。"
      : error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED"
        ? "当前授权不允许执行复验。"
        : "复验请求未完成；隔离事实保持不变。";
  return error.requestId ? `${message} 请求 ID：${error.requestId}` : message;
}

function byteSize(value: string | null): string {
  if (value === null) return "—";
  const bytes = BigInt(value);
  const units = [
    { label: "TB", value: 1_099_511_627_776n },
    { label: "GB", value: 1_073_741_824n },
    { label: "MB", value: 1_048_576n },
  ] as const;
  const unit = units.find((candidate) => bytes >= candidate.value);
  if (!unit) return `${value} B`;
  const tenths = (bytes * 10n) / unit.value;
  return `${tenths / 10n}.${tenths % 10n} ${unit.label}`;
}

function duration(seconds: string | null): string {
  if (seconds === null) return "—";
  const total = BigInt(seconds);
  return [total / 3600n, (total % 3600n) / 60n, total % 60n]
    .map((value) => value.toString().padStart(2, "0"))
    .join(":");
}

function ActiveJob({ jobId }: { readonly jobId: string }) {
  const job = useAsyncJob(jobId);
  const status = job.data?.status ?? "QUEUED";
  return (
    <Space wrap>
      <Typography.Text code>{jobId}</Typography.Text>
      <StatusTag
        status={status}
        label={status}
        tone={
          status === "SUCCEEDED"
            ? "success"
            : status === "FAILED"
              ? "danger"
              : "info"
        }
      />
      {job.connectionStatus !== "connected" ? (
        <Tag>{job.connectionStatus}</Tag>
      ) : null}
    </Space>
  );
}

export default function UploadDetailPage() {
  const { uploadId = "" } = useParams();
  const stableId =
    uploadId && !["latest", "current"].includes(uploadId) ? uploadId : null;
  const scope = useIngestScope();
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => uploadDetailQueryCodec.parse(params), [params]);
  const bootstrap = useUploadBootstrap(
    scope,
    stableId,
    capabilities.has("upload.read"),
  );
  const objects = useUploadObjects(
    scope,
    stableId,
    {
      after: search.after,
      before: search.before,
      limit: 50,
      sort: "relativePath:asc",
    },
    capabilities.has("upload.read"),
  );
  const runs = useVerificationRuns(
    scope,
    stableId,
    { limit: 50 },
    capabilities.has("upload.read"),
  );
  const events = useUploadEvents(
    scope,
    stableId,
    { eventLevel: search.eventLevel, limit: 50 },
    capabilities.has("upload.read"),
  );
  const retry = useRetryUploadVerification();
  const [confirmRetry, setConfirmRetry] = useState(false);
  const scopeKey = scope
    ? `${scope.organizationId}/${scope.projectId}/${scope.regionCode}`
    : null;
  const previousScopeKey = useRef<string | null | undefined>(undefined);
  const previousUploadId = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    ingestUploadAuthorizationVault.bindScope(scopeKey);
    if (
      previousScopeKey.current !== undefined &&
      previousScopeKey.current !== scopeKey
    ) {
      setParams(
        uploadDetailQueryCodec.build(
          updateUploadDetailSearch(search, { objectId: undefined }, true),
        ),
        { replace: true },
      );
      setConfirmRetry(false);
    }
    previousScopeKey.current = scopeKey;
  }, [scopeKey, search, setParams]);

  useEffect(() => {
    if (
      previousUploadId.current !== undefined &&
      previousUploadId.current !== stableId
    ) {
      setParams(
        uploadDetailQueryCodec.build(
          updateUploadDetailSearch(search, { objectId: undefined }, true),
        ),
        { replace: true },
      );
      setConfirmRetry(false);
    }
    previousUploadId.current = stableId;
  }, [search, setParams, stableId]);

  const retryPreflight = useMemo<DangerPreflightEvidence | null>(() => {
    if (!bootstrap.data || bootstrap.dataUpdatedAt <= 0 || !scopeKey)
      return null;
    const preparedAt = new Date(bootstrap.dataUpdatedAt);
    return {
      preparedAt: preparedAt.toISOString(),
      expiresAt: new Date(preparedAt.getTime() + 60_000).toISOString(),
      resourceVersion: bootstrap.data.session.etag,
      scopeKey,
    };
  }, [bootstrap.data, bootstrap.dataUpdatedAt, scopeKey]);

  if (!stableId)
    return (
      <main className={styles.page}>
        <PageState state="not-found" label="上传详情" />
      </main>
    );
  if (!scope)
    return (
      <main className={styles.page}>
        <PageState state="feature-unavailable" label="上传详情" />
      </main>
    );
  if (!capabilities.has("upload.read") && !capabilities.loading) {
    return (
      <main className={styles.page}>
        <PageState state="forbidden" label="上传详情" />
      </main>
    );
  }
  if (bootstrap.isPending)
    return (
      <main className={styles.page}>
        <PageState state="loading" label="上传详情" />
      </main>
    );
  if (bootstrap.isError || !bootstrap.data) {
    return (
      <main className={styles.page}>
        <PageState
          state={stateFromError(bootstrap.error)}
          label="上传详情"
          requestId={requestId(bootstrap.error)}
          onRetry={() => void bootstrap.refetch()}
        />
      </main>
    );
  }

  const session = bootstrap.data.session;
  const quarantine = bootstrap.data.latestQuarantine;
  const latestRun = bootstrap.data.latestVerificationRun;
  const pipelineErrors = latestRun ? validatePipeline(latestRun.stages) : [];
  const visibleJobIds = [
    ...new Set([
      ...session.activeJobIds,
      ...(retry.data?.jobId ? [retry.data.jobId] : []),
    ]),
  ];
  const canRetry =
    capabilities.has("upload.manage") &&
    session.allowedActions.includes("RETRY_VERIFY") &&
    quarantine !== null &&
    latestRun !== null &&
    canRequestQuarantineRelease(quarantine.disposition) &&
    pipelineErrors.every((item) => item !== "UNKNOWN_VALIDATION_STAGE");
  const apply = (next: Partial<typeof search>) => {
    setParams(uploadDetailQueryCodec.build({ ...search, ...next }, search));
  };

  const objectsState = regionState(objects);
  const objectsTable = (
    <UploadObjectsTable
      objects={objects.data?.items ?? bootstrap.data.objects}
    />
  );
  const objectsContent =
    objectsState === "ready" ? (
      objectsTable
    ) : objectsState === "refreshing" ? (
      <PageState state="refreshing" label="对象清单">
        {objectsTable}
      </PageState>
    ) : (
      <PageState
        state={objectsState}
        label="对象清单"
        requestId={requestId(objects.error)}
        onRetry={objects.isError ? () => void objects.refetch() : undefined}
      />
    );
  const runsState = regionState(runs);
  const runsList = (
    <div className={styles.itemList} role="list" aria-label="校验运行">
      {(runs.data?.items ?? []).map((run) => (
        <article
          className={styles.itemRow}
          role="listitem"
          key={run.verificationRunId}
        >
          <Space orientation="vertical" size={0}>
            <Typography.Text code>{run.verificationRunId}</Typography.Text>
            <Typography.Text type="secondary">
              {run.supersedesRunId
                ? `替代运行：${run.supersedesRunId}`
                : "首次运行"}
            </Typography.Text>
          </Space>
          <StatusTag
            status={
              typeof run.status === "string"
                ? run.status
                : `UNKNOWN (${run.status.raw})`
            }
            known={typeof run.status === "string"}
          />
        </article>
      ))}
    </div>
  );
  const runsContent =
    runsState === "ready" ? (
      runsList
    ) : runsState === "refreshing" ? (
      <PageState state="refreshing" label="重试历史">
        {runsList}
      </PageState>
    ) : (
      <PageState
        state={runsState}
        label="重试历史"
        requestId={requestId(runs.error)}
        onRetry={runs.isError ? () => void runs.refetch() : undefined}
      />
    );
  const eventsState = regionState(events);
  const timeline = <UploadEventTimeline items={events.data?.items ?? []} />;
  const eventsContent =
    eventsState === "ready" ? (
      timeline
    ) : eventsState === "refreshing" ? (
      <PageState state="refreshing" label="安全资源事件">
        {timeline}
      </PageState>
    ) : (
      <PageState
        state={eventsState}
        label="安全资源事件"
        requestId={requestId(events.error)}
        onRetry={events.isError ? () => void events.refetch() : undefined}
      />
    );
  const retryError = safeOperationError(retry.error);
  const expectedBytes = session.progress.expectedBytes;
  const overallPercent =
    expectedBytes && expectedBytes !== "0"
      ? Number(
          (BigInt(session.progress.confirmedReceivedBytes) * 10_000n) /
            BigInt(expectedBytes),
        ) / 100
      : null;

  return (
    <main className={styles.page}>
      <StandardPageScaffold
        header={{
          title: session.uploadId,
          breadcrumbs: [
            {
              key: "uploads",
              label: <Link to={routes.uploadJobs.build()}>上传任务</Link>,
            },
            { key: session.uploadId, label: session.uploadId },
          ],
          description: `${session.targetDataset?.name ?? "未指定 Dataset"} · ${session.dataSource.name} · ${session.sourceFormat}`,
          metadata: (
            <StatusTag
              status={
                typeof session.lifecycleStatus === "string"
                  ? session.lifecycleStatus
                  : `UNKNOWN (${session.lifecycleStatus.raw})`
              }
              known={typeof session.lifecycleStatus === "string"}
            />
          ),
          actions: (
            <Button
              href={routes.uploadJobs.build()}
              icon={<ArrowLeft aria-hidden="true" size={16} />}
            >
              返回任务
            </Button>
          ),
        }}
        summary={
          <section
            className={styles.summaryStrip}
            aria-labelledby="upload-summary-title"
          >
            <div className={styles.summaryProgress}>
              <div className={styles.progressHeader}>
                <div className={styles.progressIdentity}>
                  <span className={styles.progressEyebrow}>SESSION PROGRESS</span>
                  <Typography.Title id="upload-summary-title" level={2}>
                    上传处理进度
                  </Typography.Title>
                </div>
                <strong>
                  {overallPercent === null
                    ? "—"
                    : `${overallPercent.toFixed(1)}%`}
                </strong>
              </div>
              <div
                className={styles.progressTrack}
                aria-label="总体上传进度"
                aria-valuenow={overallPercent ?? undefined}
                role="progressbar"
              >
                <span style={{ width: `${overallPercent ?? 0}%` }} />
              </div>
            </div>
            <div className={styles.summaryMetrics}>
              <div>
                <Database aria-hidden="true" size={25} />
                <span>已确认 / 总大小</span>
                <strong>
                  {byteSize(session.progress.confirmedReceivedBytes)} /{" "}
                  {byteSize(session.progress.expectedBytes)}
                </strong>
              </div>
              <div>
                <CircleGauge aria-hidden="true" size={25} />
                <span>完成百分比</span>
                <strong>
                  {overallPercent === null
                    ? "—"
                    : `${overallPercent.toFixed(1)}%`}
                </strong>
              </div>
              <div>
                <Clock3 aria-hidden="true" size={25} />
                <span>预计剩余</span>
                <strong>
                  {duration(session.progress.estimatedRemainingSeconds)}
                </strong>
              </div>
              <div>
                <Boxes aria-hidden="true" size={25} />
                <span>对象进度</span>
                <strong>
                  {session.progress.completedObjects} /{" "}
                  {session.progress.totalObjects}
                </strong>
              </div>
              <div>
                <ShieldCheck aria-hidden="true" size={25} />
                <span>Multipart 分片</span>
                <strong>
                  {session.progress.completedParts} /{" "}
                  {session.progress.totalParts ?? "?"}
                </strong>
              </div>
            </div>
          </section>
        }
        filters={
          <nav
            className={styles.detailTabs}
            aria-label="上传详情分区"
            role="tablist"
          >
            {(
              [
                ["objects", "对象清单"],
                ["manifest", "Manifest"],
                ["verification", "校验结果"],
                ["events", "事件日志"],
              ] as const
            ).map(([tab, label]) => (
              <Button
                key={tab}
                id={`upload-tab-${tab}`}
                type="text"
                role="tab"
                aria-controls={`upload-tabpanel-${tab}`}
                aria-selected={search.tab === tab}
                onClick={() => apply({ tab })}
              >
                {label}
              </Button>
            ))}
          </nav>
        }
        state={
          <div className={styles.detailStack}>
            {retryError ? (
              <Alert type="error" showIcon title={retryError} />
            ) : null}
            <div
              id={`upload-tabpanel-${search.tab}`}
              className={styles.operationalGrid}
              role="tabpanel"
              aria-labelledby={
                search.tab === "parts" ? undefined : `upload-tab-${search.tab}`
              }
            >
              <div className={styles.mainColumn}>
                {search.tab === "objects" ? (
                  <section
                    className={styles.objectsPanel}
                    aria-labelledby="upload-objects-title"
                  >
                    <div className={styles.sectionHeading}>
                      <Typography.Title id="upload-objects-title" level={2}>
                        对象清单
                      </Typography.Title>
                      <Typography.Text type="secondary">
                        大表 · 游标分页 · 稳定排序
                      </Typography.Text>
                    </div>
                    <div className={styles.sectionContent}>
                      {objectsContent}
                    </div>
                    {objects.data ? (
                      <DataCursorPager
                        pageInfo={{
                          startCursor: objects.data.pageInfo.start_cursor,
                          endCursor: objects.data.pageInfo.end_cursor,
                          hasPreviousPage:
                            objects.data.pageInfo.has_previous_page,
                          hasNextPage: objects.data.pageInfo.has_next_page,
                        }}
                        busy={objects.isFetching}
                        windowLabel={`当前对象窗口 ${objects.data.items.length} 条 · 快照 ${objects.data.snapshotAt}`}
                        onChange={(cursor) => apply(cursor)}
                      />
                    ) : null}
                  </section>
                ) : null}

                {search.tab === "parts" ? (
                  <PageState
                    state="feature-unavailable"
                    title="分片详情尚未开放"
                    description="当前对象清单尚未提供可进入的分片详情入口。"
                  />
                ) : null}

                {search.tab === "manifest" ? (
                  <section
                    className={styles.manifestPanel}
                    aria-labelledby="upload-manifest-title"
                  >
                    <div className={styles.sectionHeading}>
                      <Typography.Title id="upload-manifest-title" level={2}>
                        Manifest 清单
                      </Typography.Title>
                      <Typography.Text type="secondary">
                        固定修订 · 内容摘要 · 对象集哈希
                      </Typography.Text>
                    </div>
                    <div className={styles.sectionContent}>
                      <UploadManifestSummary
                        manifest={session.sourceManifest}
                      />
                    </div>
                  </section>
                ) : null}

                {search.tab === "events" ? (
                  <section
                    className={styles.eventsPanel}
                    aria-labelledby="upload-events-title"
                  >
                    <div className={styles.sectionHeading}>
                      <Typography.Title id="upload-events-title" level={2}>
                        审计摘要
                      </Typography.Title>
                      <Space wrap>
                        <Typography.Text>
                          请求{" "}
                          <Typography.Text code>
                            {bootstrap.data.requestId}
                          </Typography.Text>
                        </Typography.Text>
                        <Typography.Text>
                          合同 {bootstrap.data.contractVersion}
                        </Typography.Text>
                      </Space>
                    </div>
                    <Alert
                      type="info"
                      showIcon
                      title="安全资源事件时间线，不是 P19 正式审计投影；仅用于 requestId/jobId 关联诊断。"
                    />
                    {visibleJobIds.length ? (
                      <div
                        className={styles.activeJobs}
                        role="list"
                        aria-label="关联异步任务"
                      >
                        {visibleJobIds.map((id) => (
                          <div role="listitem" key={id}>
                            <ActiveJob jobId={id} />
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <div className={styles.sectionContent}>{eventsContent}</div>
                  </section>
                ) : null}
              </div>

              {search.tab === "verification" ? (
                <aside
                  className={styles.validationPanel}
                  aria-label="校验与隔离摘要"
                >
                  <section>
                    <Typography.Title level={2}>
                      校验流水线阶段
                    </Typography.Title>
                    {pipelineErrors.length ? (
                      <Alert
                        type="error"
                        showIcon
                        title={`流水线合同异常：${pipelineErrors.join("、")}，相关写操作已禁用。`}
                      />
                    ) : null}
                    <div className={styles.sectionContent}>
                      <ValidationPipeline run={latestRun} />
                    </div>
                  </section>
                  <section>
                    <Typography.Title level={2}>隔离区</Typography.Title>
                    <QuarantinePanel
                      quarantine={quarantine}
                      canRequestRelease={canRetry}
                      onRequestRelease={() => setConfirmRetry(true)}
                    />
                  </section>
                  <section>
                    <Typography.Title level={2}>重试历史</Typography.Title>
                    <div className={styles.sectionContent}>{runsContent}</div>
                  </section>
                </aside>
              ) : null}
            </div>
          </div>
        }
      />
      <DangerousUploadActionDialog
        open={confirmRetry}
        title="确认复验并申请释放隔离"
        uploadId={session.uploadId}
        impact="追加新的 VerificationRun；旧运行、Finding 与隔离事实保持不可变。仅当复验和原子可用性提交成功后，服务端才释放隔离。"
        blockedReasons={session.blockedReasons}
        preflight={retryPreflight}
        currentScopeKey={scopeKey ?? ""}
        conflict={conflictFrom(retry.error)}
        pending={retry.isPending}
        onClose={() => setConfirmRetry(false)}
        onResolveConflict={() => {
          setConfirmRetry(false);
          void bootstrap.refetch();
        }}
        onConfirm={(reason) => {
          if (!latestRun) return;
          retry.mutate(
            {
              scope,
              uploadId: session.uploadId,
              etag: session.etag,
              failedVerificationRunId: latestRun.verificationRunId,
              expectedObjectSetHash: latestRun.objectSetHash,
              expectedManifestSha256: latestRun.manifestSha256,
              reason,
              idempotencyKey: createMutationIntentKey(),
            },
            { onSuccess: () => setConfirmRetry(false) },
          );
        }}
      />
    </main>
  );
}
