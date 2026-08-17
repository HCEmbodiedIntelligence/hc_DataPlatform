import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Modal, Tabs, Typography } from 'antd';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { isDatasetId, type DatasetId } from '../../entities/dataset';
import { isDatasetVersionId, type DatasetVersionId } from '../../entities/dataset-version';
import {
  useApproveReviewMutation,
  useCreateVersionDiffJobMutation,
  useEpisodeRevisionQuery,
  useOperationalInventoryQuery,
  useRequiredStorageQuery,
  useReturnReviewMutation,
  useReviewChecksMutation,
  useVersionBootstrapQuery,
  useVersionEpisodesQuery,
  useVersionManifestQuery,
  useVersionSchemaQuery,
} from '../../features/datasets/api';
import { RegionState } from '../../features/datasets/components/RegionState';
import { datasetRegionStateForError } from '../../features/datasets/components/error-state';
import { buildSuccessorDraftPendingLink } from '../../features/datasets/pending-links';
import {
  canRunReviewMutation,
  getReviewStatePolicy,
} from '../../features/datasets/review-state-machine';
import { routes, type VersionDetailTab } from '../../features/datasets/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAsyncJob } from '../../shared/jobs/use-async-job';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  DangerConfirmModal,
  DetailPageScaffold,
  EntityDrawer,
  PageState,
  StatusTag,
  UiMetricCard,
} from '../../shared/ui';
import {
  EpisodeRevisionTable,
  InventoryTable,
  ManifestCursorPager,
  ManifestTable,
  RequiredStorageTable,
  RevisionStreamTable,
  SchemaChannelTable,
  VersionCursorPager,
} from './components/VersionDetailTables';
import versionDetailQueryCodec, { type VersionDetailSearch } from './query-codec';
import styles from './styles.module.css';

const invalidDataset = 'dataset_invalid' as DatasetId;
const invalidVersion = 'version_invalid' as DatasetVersionId;
const tabs: readonly { id: VersionDetailTab; label: string }[] = [
  { id: 'revisions', label: 'Episodes' },
  { id: 'changes', label: '变更' },
  { id: 'review', label: 'Review' },
  { id: 'manifest', label: 'Manifest' },
  { id: 'schema', label: 'Schema' },
  { id: 'capacity', label: '容量' },
  { id: 'exports', label: '导出' },
];

type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

function idempotencyKey(prefix: string): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function JobStatus({ jobId }: { jobId: string }) {
  const job = useAsyncJob(jobId);
  if (job.isPending) return <RegionState state="first-loading" title="任务已接受" />;
  if (job.isError)
    return (
      <RegionState
        state={datasetRegionStateForError(job.error)}
        message={job.error instanceof Error ? job.error.message : undefined}
        onRetry={() => void job.refetch()}
      />
    );
  return (
    <section className={styles.section} aria-label="异步任务状态">
      <h2>异步任务</h2>
      <p>
        <code>{jobId}</code> · {job.data.status} · {job.connectionStatus}
      </p>
      {job.data.status === 'FAILED' ? (
        <RegionState state="partial-error" message="任务失败；Version 事实未被前端乐观改写。" />
      ) : null}
      {job.data.status === 'SUCCEEDED' ? (
        <p role="status">任务已完成；仍以重新读取的 Version Bootstrap 判定最终状态。</p>
      ) : null}
    </section>
  );
}

function MutationError({ error, onRetry }: Readonly<{ error: unknown; onRetry?: () => void }>) {
  const domain = isDomainError(error) ? error : null;
  return (
    <div>
      <RegionState
        state={datasetRegionStateForError(error)}
        message={error instanceof Error ? error.message : undefined}
        requestId={domain?.requestId}
        onRetry={onRetry}
      />
      {domain?.fieldErrors.length ? (
        <ul className={styles.fieldErrors} aria-label="字段错误">
          {domain.fieldErrors.map((item) => (
            <li key={`${item.path}:${item.code}`}>
              <code>{item.path}</code>：{item.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function rangeIsValid(startNs: string, endNs: string, lower: string, upper: string): boolean {
  if (!/^\d+$/.test(startNs) || !/^\d+$/.test(endNs)) return false;
  return (
    BigInt(startNs) >= BigInt(lower) &&
    BigInt(endNs) <= BigInt(upper) &&
    BigInt(startNs) < BigInt(endNs)
  );
}

function formText(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === 'string' ? value : '';
}

export function VersionDetailPage() {
  const raw = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const search = versionDetailQueryCodec.parse(params);
  const capabilities = useCapabilities();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const queryClient = useQueryClient();
  const handledExpiredSnapshot = useRef<string | null>(null);
  const valid = isDatasetId(raw.datasetId) && isDatasetVersionId(raw.versionId);
  const datasetId = valid ? (raw.datasetId as DatasetId) : invalidDataset;
  const versionId = valid ? (raw.versionId as DatasetVersionId) : invalidVersion;
  const bootstrap = useVersionBootstrapQuery(
    datasetId,
    versionId,
    valid && capabilities.has('dataset_version.read'),
  );
  const episodes = useVersionEpisodesQuery(
    datasetId,
    versionId,
    { ...search, snapshotToken: bootstrap.data?.snapshotToken },
    valid &&
      Boolean(bootstrap.data) &&
      capabilities.has('episode.read') &&
      search.tab === 'revisions',
  );
  const revision = useEpisodeRevisionQuery(
    datasetId,
    versionId,
    search.revisionId,
    bootstrap.data?.snapshotToken,
    valid && search.tab === 'revisions',
  );
  const manifest = useVersionManifestQuery(
    datasetId,
    versionId,
    { after: search.after, before: search.before, limit: search.limit },
    valid && search.tab === 'manifest' && capabilities.has('dataset_version.read'),
  );
  const schema = useVersionSchemaQuery(
    datasetId,
    versionId,
    bootstrap.data?.snapshotToken,
    valid && search.tab === 'schema' && capabilities.has('dataset_version.read'),
  );
  const requiredStorage = useRequiredStorageQuery(
    datasetId,
    versionId,
    bootstrap.data?.snapshotToken,
    { after: search.after, before: search.before, limit: search.limit },
    valid && search.tab === 'capacity' && capabilities.has('dataset_version.read'),
  );
  const inventory = useOperationalInventoryQuery(
    datasetId,
    versionId,
    bootstrap.data?.operationalRevision,
    { after: search.after, before: search.before, limit: search.limit },
    valid && search.tab === 'capacity' && capabilities.has('dataset_version.read'),
  );
  const checks = useReviewChecksMutation();
  const approveMutation = useApproveReviewMutation();
  const returnMutation = useReturnReviewMutation();
  const diffJob = useCreateVersionDiffJobMutation();
  const [approveOpen, setApproveOpen] = useState(false);
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnConfirmOpen, setReturnConfirmOpen] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const [approveIntentKey, setApproveIntentKey] = useState<string | null>(null);
  const [returnIntentKey, setReturnIntentKey] = useState<string | null>(null);
  const [diffIntentKey, setDiffIntentKey] = useState<string | null>(null);
  const [returnResult, setReturnResult] = useState<Awaited<
    ReturnType<typeof returnMutation.mutateAsync>
  > | null>(null);
  const [note, setNote] = useState('');
  const [severity, setSeverity] = useState<Severity>('HIGH');
  const [findingType, setFindingType] = useState('');
  const [selectedRevisionId, setSelectedRevisionId] = useState('');
  const [selectedStreamId, setSelectedStreamId] = useState('');
  const [startNs, setStartNs] = useState('0');
  const [endNs, setEndNs] = useState('1');
  const [jobId, setJobId] = useState<string | null>(null);
  const [compareTo, setCompareTo] = useState('');
  const [checksPreparedAt, setChecksPreparedAt] = useState<string | null>(null);

  const snapshotExpired = [
    episodes.error,
    revision.error,
    schema.error,
    requiredStorage.error,
  ].some((error) => isDomainError(error) && String(error.code) === 'VERSION_SNAPSHOT_EXPIRED');
  useEffect(() => {
    const expiredToken = bootstrap.data?.snapshotToken;
    if (!snapshotExpired || !expiredToken || handledExpiredSnapshot.current === expiredToken)
      return;
    handledExpiredSnapshot.current = expiredToken;
    void (async () => {
      const sameSnapshot = (query: { queryKey: readonly unknown[] }) =>
        query.queryKey[0] === 'datasets' && query.queryKey[4] === expiredToken;
      await queryClient.cancelQueries({ predicate: sameSnapshot });
      queryClient.removeQueries({ predicate: sameSnapshot });
      await bootstrap.refetch();
    })();
  }, [bootstrap, queryClient, snapshotExpired]);

  const applySearch = (changes: Partial<VersionDetailSearch>) => {
    const next = versionDetailQueryCodec.withChanges(search, changes);
    const serialized = versionDetailQueryCodec.build(next).toString();
    const base = routes.versionDetail.build({ datasetId, versionId });
    void navigate(serialized ? `${base}?${serialized}` : base);
  };

  const target = checks.data?.eligible_targets.find(
    (item) => item.output_revision_id === selectedRevisionId,
  );
  const stream = target?.streams.find((item) => item.episode_stream_id === selectedStreamId);
  const selectedFindingType = checks.data?.finding_catalog.finding_types.find(
    (item) => item.code === findingType,
  );
  const severityOptions = useMemo(() => {
    if (!checks.data || !selectedFindingType) return [] as readonly Severity[];
    const catalogOrder = checks.data.finding_catalog.severities.map((item) => item.code);
    return catalogOrder.filter((item) => selectedFindingType.allowed_severities.includes(item));
  }, [checks.data, selectedFindingType]);

  if (!valid)
    return (
      <main className={styles.page} data-page-id="P07">
        <PageState
          state="not-found"
          description="必须提供稳定 Dataset ID 与 Version ID；latest/current 不被接受。"
        />
      </main>
    );
  if (capabilities.loading || bootstrap.isPending)
    return (
      <main className={styles.page} data-page-id="P07">
        <PageState state="loading" label="版本详情" />
      </main>
    );
  if (capabilities.failed || !capabilities.has('dataset_version.read'))
    return (
      <main className={styles.page} data-page-id="P07">
        <PageState state="forbidden" />
      </main>
    );
  if (bootstrap.isError)
    return (
      <main className={styles.page} data-page-id="P07">
        <RegionState
          state={datasetRegionStateForError(bootstrap.error)}
          message={bootstrap.error instanceof Error ? bootstrap.error.message : undefined}
          onRetry={() => void bootstrap.refetch()}
        />
      </main>
    );

  const data = bootstrap.data;
  const version = data.version;
  const policy = getReviewStatePolicy(version.status, version.deliveryStatus);
  const reviewAction = data.allowedActions.find((action) => action.action === 'REVIEW_VERSION');
  const canReview = capabilities.has('dataset_version.review') && reviewAction?.allowed === true;
  const checksCurrent = Boolean(
    checks.data &&
      checks.data.version_token === version.versionToken &&
      Date.parse(checks.data.review_token_expires_at) > Date.now(),
  );
  const checksScopeKey = checks.data
    ? `${checks.data.scope.organization_id}/${checks.data.scope.project_id}/${checks.data.scope.region_code}`
    : scopeKey;
  const canApprove =
    canReview &&
    capabilities.has('dataset_version.publish') &&
    canRunReviewMutation(policy, 'APPROVE') &&
    checksCurrent &&
    checks.data?.blockers.length === 0;
  const canReturn = canReview && canRunReviewMutation(policy, 'RETURN') && checksCurrent;
  const noteLengthValid = Boolean(
    checks.data &&
      note.trim().length >= checks.data.finding_catalog.note_min_length &&
      note.trim().length <= checks.data.finding_catalog.note_max_length,
  );
  const findingValid = Boolean(
    stream &&
      selectedFindingType &&
      severityOptions.includes(severity) &&
      noteLengthValid &&
      rangeIsValid(startNs, endNs, stream.t_start_ns, stream.t_end_ns),
  );
  const returnBlockedReasons = [
    ...(!checksCurrent ? ['Review Token 已过期或 Version Token 已变化'] : []),
    ...(!target ? ['请选择预检返回的 Output Revision'] : []),
    ...(!stream ? ['请选择预检返回的 Episode Stream'] : []),
    ...(!selectedFindingType ? ['请选择当前目录中的 Finding 类型'] : []),
    ...(!severityOptions.includes(severity) ? ['严重级别不属于当前 Finding 类型'] : []),
    ...(!noteLengthValid
      ? [
          `说明长度必须在 ${checks.data?.finding_catalog.note_min_length ?? 1}–${checks.data?.finding_catalog.note_max_length ?? 8192} 字符之间`,
        ]
      : []),
    ...(stream && !rangeIsValid(startNs, endNs, stream.t_start_ns, stream.t_end_ns)
      ? [`时间范围必须位于 [${stream.t_start_ns}, ${stream.t_end_ns}) 且 start < end`]
      : []),
  ];

  const runChecks = () =>
    checks.mutate(
      { datasetId, versionId, etag: version.etag },
      {
        onSuccess: (result) => {
          setChecksPreparedAt(new Date().toISOString());
          const firstTarget =
            result.eligible_targets.length === 1 ? result.eligible_targets[0] : undefined;
          const firstStream =
            firstTarget?.streams.length === 1 ? firstTarget.streams[0] : undefined;
          const firstType = result.finding_catalog.finding_types[0];
          setSelectedRevisionId(firstTarget?.output_revision_id ?? '');
          setSelectedStreamId(firstStream?.episode_stream_id ?? '');
          setStartNs(firstStream?.t_start_ns ?? '0');
          setEndNs(firstStream?.t_end_ns ?? '1');
          setFindingType(firstType?.code ?? '');
          setSeverity(
            firstType?.allowed_severities[0] ??
              result.finding_catalog.severities[0]?.code ??
              'HIGH',
          );
          setApproveIntentKey(null);
          setReturnIntentKey(null);
          approveMutation.reset();
          returnMutation.reset();
        },
      },
    );

  const openApprove = () => {
    if (!canApprove) return;
    setApproveIntentKey(idempotencyKey('review-approve'));
    setApproveOpen(true);
  };

  const openReturn = () => {
    if (!canReturn) return;
    setReturnIntentKey(idempotencyKey('review-return'));
    setReturnOpen(true);
  };

  const confirmApprove = () => {
    if (!checks.data || !approveIntentKey || !canApprove) return;
    approveMutation.mutate(
      {
        datasetId,
        versionId,
        etag: version.etag,
        idempotencyKey: approveIntentKey,
        command: { expected_status: 'REVIEWING', review_token: checks.data.review_token },
      },
      {
        onSuccess: (result) => {
          setApproveOpen(false);
          setApproveIntentKey(null);
          setJobId(result.jobId);
        },
      },
    );
  };

  const confirmReturn = () => {
    if (!checks.data || !target || !stream || !returnIntentKey || !findingValid) return;
    returnMutation.mutate(
      {
        datasetId,
        versionId,
        expectedSourceDraftId: checks.data.source_draft_id,
        etag: version.etag,
        idempotencyKey: returnIntentKey,
        command: {
          expected_status: 'REVIEWING',
          review_token: checks.data.review_token,
          finding_catalog_version: checks.data.finding_catalog.version,
          findings: [
            {
              output_revision_id: target.output_revision_id,
              episode_stream_id: stream.episode_stream_id,
              start_ns: startNs,
              end_ns: endNs,
              finding_type: findingType,
              severity,
              note: note.trim(),
            },
          ],
        },
      },
      {
        onSuccess: (result) => {
          setReturnOpen(false);
          setReturnConfirmOpen(false);
          setReturnIntentKey(null);
          setReturnResult(result);
        },
      },
    );
  };

  const openDiff = () => {
    if (!isDatasetVersionId(compareTo) || compareTo === versionId) return;
    setDiffIntentKey(idempotencyKey('version-diff'));
    setDiffOpen(true);
  };

  const confirmDiff = () => {
    if (!isDatasetVersionId(compareTo) || compareTo === versionId || !diffIntentKey) return;
    diffJob.mutate(
      {
        datasetId,
        versionId,
        compareTo,
        snapshotToken: data.snapshotToken,
        etag: version.etag,
        idempotencyKey: diffIntentKey,
      },
      {
        onSuccess: (result) => {
          setDiffOpen(false);
          setDiffIntentKey(null);
          setJobId(result);
        },
      },
    );
  };

  const versionFacts = (
    <div className={styles.versionFacts}>
      <Typography.Title level={2}>固定版本事实</Typography.Title>
      <dl>
        <dt>Version</dt>
        <dd title={versionId}>
          <code>{versionId}</code>
        </dd>
        <dt>Dataset</dt>
        <dd title={datasetId}>
          <code>{datasetId}</code>
        </dd>
        <dt>Manifest Token</dt>
        <dd title={data.snapshotToken}>
          <code>{data.snapshotToken}</code>
        </dd>
        <dt>Schema</dt>
        <dd title={schema.data?.snapshot.id ?? '切换到 Schema 后加载'}>
          {schema.data?.snapshot.id ?? '切换到 Schema 后加载'}
        </dd>
        <dt>Operational</dt>
        <dd title={data.operationalRevision}>
          <code>{data.operationalRevision}</code>
        </dd>
        <dt>发布状态</dt>
        <dd>
          <StatusTag
            status={version.status}
            tone={
              version.status === 'READY'
                ? 'success'
                : version.status === 'RETURNED'
                  ? 'danger'
                  : 'info'
            }
            known={version.status !== 'UNKNOWN'}
          />
        </dd>
      </dl>
      <Button type="primary" block onClick={() => applySearch({ tab: 'manifest' })}>
        查看 Manifest
      </Button>
      <Button block onClick={() => applySearch({ tab: 'schema' })}>
        查看 Schema
      </Button>
    </div>
  );

  return (
    <main className={styles.page} data-page-id="P07">
      <DetailPageScaffold
        resourceId={versionId}
        header={{
          title: version.displayVersion,
          description: '内容快照与 operational revision 分离，均绑定固定 Version。',
          breadcrumbs: [
            { key: 'assets', label: '数据资产', to: routes.datasets.build() },
            {
              key: datasetId,
              label: <code>{datasetId}</code>,
              to: routes.datasetDetail.build({ datasetId }),
            },
            { key: versionId, label: version.displayVersion },
          ],
          metadata: (
            <StatusTag
              status={version.status}
              tone={
                version.status === 'READY'
                  ? 'success'
                  : version.status === 'RETURNED'
                    ? 'danger'
                    : version.status === 'REVIEWING'
                      ? 'info'
                      : 'warning'
              }
              known={version.status !== 'UNKNOWN'}
            />
          ),
          actions: search.returnTo ? (
            <Button onClick={() => void navigate(search.returnTo!)}>返回数据集</Button>
          ) : undefined,
        }}
        tabs={
          <Tabs
            activeKey={search.tab}
            items={tabs.map((tab) => ({ key: tab.id, label: tab.label }))}
            more={{
              icon: (
                <>
                  <span aria-hidden="true">•••</span>
                  <span className={styles.srOnly}>更多版本详情标签页</span>
                </>
              ),
            }}
            onChange={(key) => applySearch({ tab: key as VersionDetailTab })}
          />
        }
        inspector={versionFacts}
        inspectorLabel="固定版本事实"
      >
        <div className={styles.stack}>
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <div>
                <Typography.Title level={2}>版本概要</Typography.Title>
                <Typography.Paragraph>
                  所有身份和令牌均来自固定 Version Bootstrap。
                </Typography.Paragraph>
              </div>
            </div>
            <div className={styles.versionSummaryBand} aria-label="版本关键事实">
              <div>
                <span>类型</span>
                <strong>{version.kind}</strong>
              </div>
              <div>
                <span>状态</span>
                <StatusTag
                  status={version.status}
                  tone={
                    version.status === 'READY'
                      ? 'success'
                      : version.status === 'RETURNED'
                        ? 'danger'
                        : 'info'
                  }
                  known={version.status !== 'UNKNOWN'}
                />
              </div>
              <div>
                <span>Delivery</span>
                <StatusTag
                  status={version.deliveryStatus ?? 'NONE'}
                  tone={
                    version.deliveryStatus === 'CANDIDATE_READY'
                      ? 'success'
                      : version.deliveryStatus === 'GENERATING'
                        ? 'info'
                        : 'neutral'
                  }
                  known={version.deliveryStatus !== 'UNKNOWN'}
                />
              </div>
              <div>
                <span>Snapshot token</span>
                <code title={data.snapshotToken}>{data.snapshotToken}</code>
              </div>
              <div>
                <span>Operational revision</span>
                <code title={data.operationalRevision}>{data.operationalRevision}</code>
              </div>
            </div>
          </section>
          {policy.unknownEnum ? <PageState state="unknown" description={policy.reason} /> : null}

          {search.tab === 'revisions' ? (
            <section className={styles.section}>
              <div className={styles.sectionHeader}>
                <div>
                  <h2>Episodes / Revisions</h2>
                  <p>表格、筛选和游标均绑定同一个 snapshot token。</p>
                </div>
              </div>
              <form
                className={styles.actions}
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = new FormData(event.currentTarget);
                  applySearch({
                    q: formText(form, 'q').trim() || undefined,
                    included:
                      form.get('included') === 'all' ? undefined : form.get('included') === 'true',
                    hasFinding:
                      form.get('hasFinding') === 'all'
                        ? undefined
                        : form.get('hasFinding') === 'true',
                    limit: Number(form.get('limit')) as VersionDetailSearch['limit'],
                  });
                }}
              >
                <label className={styles.filterField}>
                  搜索
                  <input name="q" defaultValue={search.q} />
                </label>
                <label className={styles.filterField}>
                  Included
                  <select
                    name="included"
                    defaultValue={search.included === undefined ? 'all' : String(search.included)}
                  >
                    <option value="all">全部</option>
                    <option value="true">是</option>
                    <option value="false">否</option>
                  </select>
                </label>
                <label className={styles.filterField}>
                  Finding
                  <select
                    name="hasFinding"
                    defaultValue={
                      search.hasFinding === undefined ? 'all' : String(search.hasFinding)
                    }
                  >
                    <option value="all">全部</option>
                    <option value="true">有</option>
                    <option value="false">无</option>
                  </select>
                </label>
                <label className={styles.filterField}>
                  每页
                  <select name="limit" defaultValue={search.limit}>
                    <option value="20">20</option>
                    <option value="50">50</option>
                    <option value="100">100</option>
                  </select>
                </label>
                <Button type="primary" htmlType="submit">
                  应用
                </Button>
              </form>
              {episodes.isPending ? (
                <RegionState state="first-loading" />
              ) : episodes.isError ? (
                <RegionState
                  state={datasetRegionStateForError(episodes.error)}
                  onRetry={() => void episodes.refetch()}
                />
              ) : episodes.data.items.length === 0 ? (
                <RegionState state="empty" />
              ) : (
                <>
                  <EpisodeRevisionTable
                    items={episodes.data.items}
                    onInspect={(episode) => applySearch({ revisionId: episode.selectedRevisionId })}
                    onOpenViewer={(episode) =>
                      void navigate(
                        routes.episodeViewer.build({
                          datasetId,
                          versionId,
                          episodeId: episode.episodeId,
                          returnTo: `${location.pathname}${location.search}`,
                        }),
                      )
                    }
                  />
                  <VersionCursorPager
                    page={episodes.data}
                    busy={episodes.isFetching}
                    onChange={applySearch}
                  />
                </>
              )}
            </section>
          ) : null}

          {search.tab === 'review' ? (
            <section className={styles.section}>
              <div className={styles.sectionHeader}>
                <div>
                  <h2>Review 决定与 Findings</h2>
                  <p>P07 是唯一 mutation Owner；Finding 是不可变复核事实，不是 ManualIssue。</p>
                </div>
                <div className={styles.actions}>
                  <Button disabled={!canReview || checks.isPending} onClick={runChecks}>
                    {checks.isPending ? '预检中…' : '运行 Review 预检'}
                  </Button>
                  <Button type="primary" disabled={!canApprove} onClick={openApprove}>
                    复核通过
                  </Button>
                  <Button danger type="primary" disabled={!canReturn} onClick={openReturn}>
                    退回
                  </Button>
                </div>
              </div>
              {!canReview ? (
                <RegionState
                  state={policy.unknownEnum ? 'unknown-enum' : 'feature-unavailable'}
                  message={
                    reviewAction?.blockedReasons.map((reason) => reason.message).join('；') ||
                    policy.reason
                  }
                />
              ) : null}
              {checks.isError ? <MutationError error={checks.error} onRetry={runChecks} /> : null}
              {checks.data ? (
                <div className={styles.stack}>
                  <p>
                    Catalog <code>{checks.data.finding_catalog.version}</code> · Token 到期{' '}
                    {new Date(checks.data.review_token_expires_at).toLocaleString()}
                  </p>
                  {!checksCurrent ? (
                    <RegionState
                      state="conflict"
                      message="Review Token 已过期或 Version Token 已变化；请重新运行预检。"
                    />
                  ) : null}
                  {checks.data.blockers.length ? (
                    <Alert
                      type="warning"
                      showIcon
                      title="复核通过阻断原因"
                      description={
                        <ul>
                          {checks.data.blockers.map((item) => (
                            <li key={item.code}>
                              {item.code}：{item.message}
                            </li>
                          ))}
                        </ul>
                      }
                    />
                  ) : null}
                </div>
              ) : null}
              {returnResult && !data.returnLineage ? (
                <Card title="退回事务已完成" role="status">
                  <p>
                    Decision <code>{returnResult.reviewDecision.id}</code>
                  </p>
                  <p>
                    Findings:{' '}
                    {returnResult.reviewFindingIds.map((id) => (
                      <code key={id}>{id} </code>
                    ))}
                  </p>
                  <p>
                    Lineage: <code>{returnResult.supersedesDraftId}</code> →{' '}
                    <code>{returnResult.successorDraftId}</code>
                  </p>
                  <Button
                    type="primary"
                    onClick={() => {
                      void navigate(buildSuccessorDraftPendingLink(returnResult.successorDraftId));
                    }}
                  >
                    继续返工
                  </Button>
                </Card>
              ) : null}
              {data.returnLineage ? (
                <Card title="不可变退回事实">
                  <p>
                    Decision <code>{data.returnLineage.reviewDecisionId}</code>
                  </p>
                  <p>
                    Findings:{' '}
                    {data.returnLineage.reviewFindingIds.map((id) => (
                      <code key={id}>{id} </code>
                    ))}
                  </p>
                  <p>
                    Lineage: <code>{data.returnLineage.supersedesDraftId}</code> →{' '}
                    <code>{data.returnLineage.successorDraftId}</code>
                  </p>
                  <Button
                    type="primary"
                    onClick={() => {
                      void navigate(
                        buildSuccessorDraftPendingLink(data.returnLineage!.successorDraftId),
                      );
                    }}
                  >
                    继续返工
                  </Button>
                </Card>
              ) : null}
              {approveMutation.isError ? <MutationError error={approveMutation.error} /> : null}
              {returnMutation.isError ? (
                <MutationError
                  error={returnMutation.error}
                  onRetry={
                    isDomainError(returnMutation.error) && returnMutation.error.retryable
                      ? confirmReturn
                      : undefined
                  }
                />
              ) : null}
              {jobId ? <JobStatus jobId={jobId} /> : null}
            </section>
          ) : null}

          {search.tab === 'manifest' ? (
            <section className={styles.section}>
              <h2>Manifest 摘要</h2>
              <p>Manifest 原文、对象路径和授权 URL 不进入遥测或 Query Key。</p>
              {manifest.isPending ? (
                <RegionState state="first-loading" />
              ) : manifest.isError ? (
                <RegionState
                  state={datasetRegionStateForError(manifest.error)}
                  onRetry={() => void manifest.refetch()}
                />
              ) : (
                <>
                  <p>
                    <code>{manifest.data.manifest.manifest_id}</code> · SHA-256{' '}
                    {manifest.data.manifest.sha256}
                  </p>
                  {manifest.data.items.length === 0 ? (
                    <RegionState state="empty" />
                  ) : (
                    <>
                      <ManifestTable items={manifest.data.items} />
                      <ManifestCursorPager
                        pageInfo={manifest.data.page_info}
                        count={manifest.data.items.length}
                        snapshotAt={manifest.data.snapshot_at}
                        busy={manifest.isFetching}
                        onChange={applySearch}
                      />
                    </>
                  )}
                </>
              )}
            </section>
          ) : null}

          {search.tab === 'changes' ? (
            <section className={styles.section}>
              <h2>Version Diff</h2>
              <p>比较对象必须是同一 Dataset 的固定 Version ID；不接受 latest/current。</p>
              <label className={styles.filterField}>
                比较 Version ID
                <input value={compareTo} onChange={(event) => setCompareTo(event.target.value)} />
              </label>
              <Button
                type="primary"
                disabled={
                  !isDatasetVersionId(compareTo) || compareTo === versionId || diffJob.isPending
                }
                onClick={openDiff}
              >
                {diffJob.isPending ? '提交中…' : '创建 Diff Job'}
              </Button>
              {diffJob.isError ? <MutationError error={diffJob.error} /> : null}
              {jobId ? <JobStatus jobId={jobId} /> : null}
            </section>
          ) : null}
          {search.tab === 'schema' ? (
            <section className={styles.section}>
              <h2>Schema Snapshot</h2>
              <p>Schema 与当前固定 Version 的内容快照绑定；合同不匹配时整区 fail closed。</p>
              {schema.isPending ? (
                <RegionState state="first-loading" />
              ) : schema.isError ? (
                <RegionState
                  state={datasetRegionStateForError(schema.error)}
                  onRetry={() => void schema.refetch()}
                />
              ) : (
                <>
                  <div className={styles.metricGrid}>
                    <UiMetricCard
                      label="Reference"
                      value={`${schema.data.snapshot.type} / ${schema.data.snapshot.id}`}
                    />
                    <UiMetricCard label="Schema Version" value={schema.data.snapshot.version} />
                    <UiMetricCard label="Channels" value={schema.data.channelCount} />
                    <UiMetricCard
                      label="SHA-256"
                      value={`${schema.data.snapshot.sha256.slice(0, 16)}…`}
                    />
                  </div>
                  {schema.data.channels.length === 0 ? (
                    <RegionState state="empty" />
                  ) : (
                    <SchemaChannelTable items={schema.data.channels} />
                  )}
                </>
              )}
            </section>
          ) : null}
          {search.tab === 'capacity' ? (
            <section className={styles.section}>
              <h2>容量与运营库存</h2>
              <p>
                Required storage 绑定内容快照，operational inventory 绑定{' '}
                <code>{data.operationalRevision}</code>；两者不会在前端合并推算。
              </p>
              <h3>Required storage</h3>
              {requiredStorage.isPending ? (
                <RegionState state="first-loading" />
              ) : requiredStorage.isError ? (
                <RegionState
                  state={datasetRegionStateForError(requiredStorage.error)}
                  onRetry={() => void requiredStorage.refetch()}
                />
              ) : requiredStorage.data.items.length === 0 ? (
                <RegionState state="empty" />
              ) : (
                <>
                  <RequiredStorageTable items={requiredStorage.data.items} />
                  <VersionCursorPager
                    page={requiredStorage.data}
                    busy={requiredStorage.isFetching}
                    onChange={applySearch}
                  />
                </>
              )}
              <h3>Operational inventory</h3>
              {inventory.isPending ? (
                <RegionState state="first-loading" />
              ) : inventory.isError ? (
                <RegionState
                  state={datasetRegionStateForError(inventory.error)}
                  onRetry={() => void inventory.refetch()}
                />
              ) : inventory.data.items.length === 0 ? (
                <RegionState state="empty" />
              ) : (
                <>
                  <InventoryTable items={inventory.data.items} />
                  <VersionCursorPager
                    page={inventory.data}
                    busy={inventory.isFetching}
                    onChange={applySearch}
                  />
                </>
              )}
            </section>
          ) : null}
          {search.tab === 'exports' ? (
            <section className={styles.section}>
              <h2>导出</h2>
              <p>导出执行状态、结果可用性和下载授权是三个独立状态轴。</p>
              <RegionState
                state="feature-unavailable"
                message="下载授权合同未冻结；页面不会缓存或猜测签名 URL。"
              />
            </section>
          ) : null}
        </div>
      </DetailPageScaffold>

      <EntityDrawer
        open={Boolean(search.revisionId)}
        title={<Typography.Title level={2}>Revision Inspector</Typography.Title>}
        loading={revision.isPending}
        onClose={() => applySearch({ revisionId: undefined })}
      >
        {revision.isError ? (
          <RegionState
            state={datasetRegionStateForError(revision.error)}
            onRetry={() => void revision.refetch()}
          />
        ) : revision.data ? (
          <div className={styles.drawerBody}>
            <dl>
              <dt>Revision</dt>
              <dd>
                <code>{search.revisionId}</code>
              </dd>
              <dt>Episode</dt>
              <dd>{revision.data.episode_id}</dd>
              <dt>Ordinal</dt>
              <dd>{revision.data.ordinal}</dd>
              <dt>Duration ns</dt>
              <dd>{revision.data.duration_ns}</dd>
              <dt>SHA-256</dt>
              <dd>{revision.data.content_sha256.slice(0, 16)}…</dd>
            </dl>
            <RevisionStreamTable items={revision.data.streams} />
          </div>
        ) : null}
      </EntityDrawer>

      <DangerConfirmModal
        open={approveOpen}
        title="确认复核通过"
        actionLabel="确认复核通过"
        resourceId={versionId}
        impact={[
          '创建不可变 ReviewDecision',
          '启动 Manifest/发布异步任务',
          'Version 保持 REVIEWING，直到完整 Bootstrap 证明 READY',
        ]}
        blockers={checks.data?.blockers}
        preflight={
          checks.data && checksPreparedAt
            ? {
                preparedAt: checksPreparedAt,
                expiresAt: checks.data.review_token_expires_at,
                resourceVersion: checks.data.version_token,
                scopeKey: checksScopeKey,
              }
            : null
        }
        currentScopeKey={checksScopeKey}
        pending={approveMutation.isPending}
        conflict={
          isDomainError(approveMutation.error) &&
          (approveMutation.error.httpStatus === 409 || approveMutation.error.httpStatus === 412)
            ? { status: approveMutation.error.httpStatus, code: approveMutation.error.code }
            : null
        }
        onConfirm={confirmApprove}
        onCancel={() => {
          if (!approveMutation.isPending) {
            setApproveOpen(false);
            setApproveIntentKey(null);
          }
        }}
      />

      <Modal
        open={diffOpen}
        title="确认创建版本 Diff Job"
        closable={!diffJob.isPending}
        mask={{ closable: false }}
        onCancel={() => {
          if (!diffJob.isPending) {
            setDiffOpen(false);
            setDiffIntentKey(null);
          }
        }}
        footer={[
          <Button key="cancel" disabled={diffJob.isPending} onClick={() => setDiffOpen(false)}>
            取消
          </Button>,
          <Button
            key="confirm"
            type="primary"
            loading={diffJob.isPending}
            disabled={!isDatasetVersionId(compareTo) || compareTo === versionId}
            onClick={confirmDiff}
          >
            确认创建 Diff Job
          </Button>,
        ]}
      >
        <Typography.Paragraph>
          异步任务只比较两个固定 Version 的当前内容快照，不会改写 Version 事实。
        </Typography.Paragraph>
        <Typography.Paragraph>
          <code>{versionId}</code> → <code>{compareTo}</code>
        </Typography.Paragraph>
      </Modal>

      <Modal
        open={returnOpen}
        title="构造结构化 Finding"
        footer={[
          <Button
            key="cancel"
            onClick={() => {
              setReturnOpen(false);
              setReturnIntentKey(null);
            }}
          >
            取消
          </Button>,
          <Button
            key="next"
            danger
            type="primary"
            disabled={!findingValid || !canReturn}
            onClick={() => {
              setReturnOpen(false);
              setReturnConfirmOpen(true);
            }}
          >
            进入最终确认
          </Button>,
        ]}
        mask={{ closable: false }}
        onCancel={() => {
          setReturnOpen(false);
          setReturnIntentKey(null);
        }}
      >
        <div className={styles.reviewForm}>
          <label>
            Output Revision
            <select
              value={selectedRevisionId}
              onChange={(event) => {
                const nextTarget = checks.data?.eligible_targets.find(
                  (item) => item.output_revision_id === event.target.value,
                );
                const nextStream =
                  nextTarget?.streams.length === 1 ? nextTarget.streams[0] : undefined;
                setSelectedRevisionId(event.target.value);
                setSelectedStreamId(nextStream?.episode_stream_id ?? '');
                setStartNs(nextStream?.t_start_ns ?? '0');
                setEndNs(nextStream?.t_end_ns ?? '1');
              }}
            >
              <option value="">请选择固定 Revision</option>
              {checks.data?.eligible_targets.map((item) => (
                <option key={item.output_revision_id} value={item.output_revision_id}>
                  {item.output_revision_id} · {item.episode_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Episode Stream
            <select
              value={selectedStreamId}
              onChange={(event) => {
                const nextStream = target?.streams.find(
                  (item) => item.episode_stream_id === event.target.value,
                );
                setSelectedStreamId(event.target.value);
                setStartNs(nextStream?.t_start_ns ?? '0');
                setEndNs(nextStream?.t_end_ns ?? '1');
              }}
            >
              <option value="">请选择稳定 Stream</option>
              {target?.streams.map((item) => (
                <option key={item.episode_stream_id} value={item.episode_stream_id}>
                  {item.channel_path} · {item.episode_stream_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Finding 类型
            <select
              value={findingType}
              onChange={(event) => {
                const next = checks.data?.finding_catalog.finding_types.find(
                  (item) => item.code === event.target.value,
                );
                setFindingType(event.target.value);
                setSeverity(next?.allowed_severities[0] ?? 'HIGH');
              }}
            >
              {checks.data?.finding_catalog.finding_types.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.label} ({item.code})
                </option>
              ))}
            </select>
          </label>
          <label>
            严重级别
            <select
              value={severity}
              onChange={(event) => setSeverity(event.target.value as Severity)}
            >
              {severityOptions.map((item) => (
                <option key={item} value={item}>
                  {checks.data?.finding_catalog.severities.find((entry) => entry.code === item)
                    ?.label ?? item}
                </option>
              ))}
            </select>
          </label>
          <label>
            开始 ns
            <input
              inputMode="numeric"
              value={startNs}
              onChange={(event) => setStartNs(event.target.value)}
            />
          </label>
          <label>
            结束 ns
            <input
              inputMode="numeric"
              value={endNs}
              onChange={(event) => setEndNs(event.target.value)}
            />
          </label>
          <label>
            说明
            <textarea
              required
              minLength={checks.data?.finding_catalog.note_min_length}
              maxLength={checks.data?.finding_catalog.note_max_length}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          {stream ? (
            <small>
              合法范围：[{stream.t_start_ns}, {stream.t_end_ns})
            </small>
          ) : null}
          {returnBlockedReasons.length ? (
            <Alert
              type="warning"
              showIcon
              title="Finding 尚未满足提交条件"
              description={
                <ul>
                  {returnBlockedReasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              }
            />
          ) : null}
        </div>
      </Modal>

      <DangerConfirmModal
        open={returnConfirmOpen}
        title="退回固定 Version"
        actionLabel="确认原子退回"
        resourceId={versionId}
        impact={[
          '当前 Version 进入终态 RETURNED',
          'Review Decision 与 Finding 永久不可变',
          '创建唯一 successor Draft；前端只使用响应 draftId 跳转',
        ]}
        blockers={
          findingValid
            ? []
            : returnBlockedReasons.map((message) => ({ code: 'FINDING_INVALID', message }))
        }
        preflight={
          checks.data && checksPreparedAt
            ? {
                preparedAt: checksPreparedAt,
                expiresAt: checks.data.review_token_expires_at,
                resourceVersion: checks.data.version_token,
                scopeKey: checksScopeKey,
              }
            : null
        }
        currentScopeKey={checksScopeKey}
        pending={returnMutation.isPending}
        conflict={
          isDomainError(returnMutation.error) &&
          (returnMutation.error.httpStatus === 409 || returnMutation.error.httpStatus === 412)
            ? { status: returnMutation.error.httpStatus, code: returnMutation.error.code }
            : null
        }
        onConfirm={confirmReturn}
        onCancel={() => {
          if (!returnMutation.isPending) {
            setReturnConfirmOpen(false);
            setReturnIntentKey(null);
          }
        }}
      />
    </main>
  );
}

export default VersionDetailPage;
