import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
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
import { ConfirmDialog } from '../../features/datasets/components/ConfirmDialog';
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
import versionDetailQueryCodec, { type VersionDetailSearch } from './query-codec';
import '../../features/datasets/components/datasets.css';

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
    <section className="dataset-band" aria-label="异步任务状态">
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
        <ul className="dataset-field-errors" aria-label="字段错误">
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
      <main className="dataset-page" data-page-id="P07">
        <RegionState
          state="not-found"
          message="必须提供稳定 Dataset ID 与 Version ID；latest/current 不被接受。"
        />
      </main>
    );
  if (capabilities.loading || bootstrap.isPending)
    return (
      <main className="dataset-page">
        <RegionState state="first-loading" />
      </main>
    );
  if (capabilities.failed || !capabilities.has('dataset_version.read'))
    return (
      <main className="dataset-page">
        <RegionState state="forbidden" />
      </main>
    );
  if (bootstrap.isError)
    return (
      <main className="dataset-page">
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

  return (
    <main className="dataset-page" data-page-id="P07">
      <header className="dataset-resource-header">
        <div>
          <p className="dataset-eyebrow">Immutable version</p>
          <h1>{version.displayVersion}</h1>
          <p>
            <code>{datasetId}</code> / <code>{versionId}</code>
          </p>
        </div>
        <div className="dataset-actions">
          <span className={`dataset-status dataset-status--${version.status.toLowerCase()}`}>
            {version.status}
          </span>
          {search.returnTo ? (
            <button
              type="button"
              className="dataset-button dataset-button--secondary"
              onClick={() => {
                void navigate(search.returnTo!);
              }}
            >
              返回数据集
            </button>
          ) : null}
        </div>
      </header>
      <nav className="dataset-tabs" aria-label="版本详情分区">
        {tabs.map((tab) => (
          <button
            type="button"
            className="dataset-tab"
            role="tab"
            aria-selected={search.tab === tab.id}
            key={tab.id}
            onClick={() => applySearch({ tab: tab.id })}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <section className="dataset-band">
        <div className="dataset-band-heading">
          <div>
            <h2>版本概要</h2>
            <p>内容快照与 operational revision 分离，均绑定固定 Version。</p>
          </div>
        </div>
        <dl className="dataset-metrics">
          <div className="dataset-metric">
            <dt>类型</dt>
            <dd>{version.kind}</dd>
          </div>
          <div className="dataset-metric">
            <dt>状态</dt>
            <dd>{version.status}</dd>
          </div>
          <div className="dataset-metric">
            <dt>Delivery</dt>
            <dd>{version.deliveryStatus ?? '—'}</dd>
          </div>
          <div className="dataset-metric">
            <dt>Snapshot</dt>
            <dd title={data.snapshotToken}>{data.snapshotToken.slice(0, 12)}…</dd>
          </div>
          <div className="dataset-metric">
            <dt>Operational</dt>
            <dd>{data.operationalRevision}</dd>
          </div>
        </dl>
      </section>
      {policy.unknownEnum ? <RegionState state="unknown-enum" message={policy.reason} /> : null}

      {search.tab === 'revisions' ? (
        <section className="dataset-band">
          <div className="dataset-band-heading">
            <div>
              <h2>Episodes / Revisions</h2>
              <p>表格、筛选和游标均绑定同一个 snapshot token。</p>
            </div>
          </div>
          <form
            className="dataset-inline-filters"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              applySearch({
                q: formText(form, 'q').trim() || undefined,
                included:
                  form.get('included') === 'all' ? undefined : form.get('included') === 'true',
                hasFinding:
                  form.get('hasFinding') === 'all' ? undefined : form.get('hasFinding') === 'true',
                limit: Number(form.get('limit')) as VersionDetailSearch['limit'],
              });
            }}
          >
            <label>
              搜索
              <input name="q" defaultValue={search.q} />
            </label>
            <label>
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
            <label>
              Finding
              <select
                name="hasFinding"
                defaultValue={search.hasFinding === undefined ? 'all' : String(search.hasFinding)}
              >
                <option value="all">全部</option>
                <option value="true">有</option>
                <option value="false">无</option>
              </select>
            </label>
            <label>
              每页
              <select name="limit" defaultValue={search.limit}>
                <option value="20">20</option>
                <option value="50">50</option>
                <option value="100">100</option>
              </select>
            </label>
            <button type="submit" className="dataset-button">
              应用
            </button>
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
              <div className="dataset-table-scroll">
                <table className="dataset-table">
                  <thead>
                    <tr>
                      <th>Episode</th>
                      <th>Revision</th>
                      <th>Included</th>
                      <th>Review</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {episodes.data.items.map((episode) => (
                      <tr key={episode.episodeId}>
                        <td>{episode.episodeId}</td>
                        <td>
                          <button
                            type="button"
                            className="dataset-link"
                            onClick={() => applySearch({ revisionId: episode.selectedRevisionId })}
                          >
                            {episode.selectedRevisionId}
                          </button>
                        </td>
                        <td>{episode.included ? '是' : '否'}</td>
                        <td>
                          {episode.reviewStatus} · {episode.reviewFindingCount}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="dataset-link"
                            onClick={() => {
                              void navigate(
                                routes.episodeViewer.build({
                                  datasetId,
                                  versionId,
                                  episodeId: episode.episodeId,
                                  returnTo: `${location.pathname}${location.search}`,
                                }),
                              );
                            }}
                          >
                            只读 Viewer
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="dataset-table-footer">
                <span>快照 {episodes.data.snapshotAt}</span>
                <nav className="dataset-pagination">
                  <button
                    type="button"
                    className="dataset-button dataset-button--secondary"
                    disabled={!episodes.data.pageInfo.hasPreviousPage}
                    onClick={() =>
                      applySearch({
                        before: episodes.data.pageInfo.before ?? undefined,
                        after: undefined,
                      })
                    }
                  >
                    上一组
                  </button>
                  <button
                    type="button"
                    className="dataset-button dataset-button--secondary"
                    disabled={!episodes.data.pageInfo.hasNextPage}
                    onClick={() =>
                      applySearch({
                        after: episodes.data.pageInfo.after ?? undefined,
                        before: undefined,
                      })
                    }
                  >
                    下一组
                  </button>
                </nav>
              </div>
            </>
          )}
          {search.revisionId ? (
            <aside className="dataset-blocked-reasons" aria-label="Revision Inspector">
              <div className="dataset-band-heading">
                <div>
                  <h3>Revision Inspector</h3>
                  <p>
                    <code>{search.revisionId}</code> · 绑定当前内容快照
                  </p>
                </div>
                <button
                  type="button"
                  className="dataset-button dataset-button--secondary"
                  onClick={() => applySearch({ revisionId: undefined })}
                >
                  关闭
                </button>
              </div>
              {revision.isPending ? (
                <RegionState state="first-loading" />
              ) : revision.isError ? (
                <RegionState
                  state={datasetRegionStateForError(revision.error)}
                  onRetry={() => void revision.refetch()}
                />
              ) : (
                <>
                  <dl className="dataset-metrics">
                    <div className="dataset-metric">
                      <dt>Episode</dt>
                      <dd>{revision.data.episode_id}</dd>
                    </div>
                    <div className="dataset-metric">
                      <dt>Ordinal</dt>
                      <dd>{revision.data.ordinal}</dd>
                    </div>
                    <div className="dataset-metric">
                      <dt>Duration ns</dt>
                      <dd>{revision.data.duration_ns}</dd>
                    </div>
                    <div className="dataset-metric">
                      <dt>SHA-256</dt>
                      <dd title={revision.data.content_sha256}>
                        {revision.data.content_sha256.slice(0, 16)}…
                      </dd>
                    </div>
                  </dl>
                  <div className="dataset-table-scroll">
                    <table className="dataset-table">
                      <thead>
                        <tr>
                          <th>Stream</th>
                          <th>Channel</th>
                          <th>Kind</th>
                          <th>Range ns</th>
                        </tr>
                      </thead>
                      <tbody>
                        {revision.data.streams.map((item) => (
                          <tr key={item.episode_stream_id}>
                            <td>{item.episode_stream_id}</td>
                            <td>{item.channel_path}</td>
                            <td>{item.kind}</td>
                            <td>
                              {item.t_start_ns}–{item.t_end_ns}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </aside>
          ) : null}
        </section>
      ) : null}

      {search.tab === 'review' ? (
        <section className="dataset-band">
          <div className="dataset-band-heading">
            <div>
              <h2>Review 决定与 Findings</h2>
              <p>P07 是唯一 mutation Owner；Finding 是不可变复核事实，不是 ManualIssue。</p>
            </div>
            <div className="dataset-actions">
              <button
                type="button"
                className="dataset-button dataset-button--secondary"
                disabled={!canReview || checks.isPending}
                onClick={runChecks}
              >
                {checks.isPending ? '预检中…' : '运行 Review 预检'}
              </button>
              <button
                type="button"
                className="dataset-button"
                disabled={!canApprove}
                onClick={openApprove}
              >
                复核通过
              </button>
              <button
                type="button"
                className="dataset-button dataset-button--danger"
                disabled={!canReturn}
                onClick={openReturn}
              >
                退回
              </button>
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
            <div className="dataset-review-checks">
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
                <div className="dataset-blocked-reasons">
                  <h3>复核通过阻断原因</h3>
                  <ul>
                    {checks.data.blockers.map((item) => (
                      <li key={item.code}>
                        {item.code}：{item.message}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
          {returnResult && !data.returnLineage ? (
            <div className="dataset-blocked-reasons" role="status">
              <h3>退回事务已完成</h3>
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
              <button
                type="button"
                className="dataset-button"
                onClick={() => {
                  void navigate(buildSuccessorDraftPendingLink(returnResult.successorDraftId));
                }}
              >
                继续返工
              </button>
            </div>
          ) : null}
          {data.returnLineage ? (
            <div className="dataset-blocked-reasons">
              <h3>不可变退回事实</h3>
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
              <button
                type="button"
                className="dataset-button"
                onClick={() => {
                  void navigate(
                    buildSuccessorDraftPendingLink(data.returnLineage!.successorDraftId),
                  );
                }}
              >
                继续返工
              </button>
            </div>
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
        <section className="dataset-band">
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
                <div className="dataset-table-scroll">
                  <table className="dataset-table">
                    <thead>
                      <tr>
                        <th>Entry</th>
                        <th>Episode</th>
                        <th>Role</th>
                        <th>Bytes</th>
                        <th>SHA-256</th>
                      </tr>
                    </thead>
                    <tbody>
                      {manifest.data.items.map((item) => (
                        <tr key={item.entry_id}>
                          <td>{item.entry_id}</td>
                          <td>{item.episode_id}</td>
                          <td>{item.role}</td>
                          <td>{item.size_bytes}</td>
                          <td>
                            <code>{item.sha256.slice(0, 16)}…</code>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      ) : null}

      {search.tab === 'changes' ? (
        <section className="dataset-band">
          <h2>Version Diff</h2>
          <p>比较对象必须是同一 Dataset 的固定 Version ID；不接受 latest/current。</p>
          <label>
            比较 Version ID
            <input value={compareTo} onChange={(event) => setCompareTo(event.target.value)} />
          </label>
          <button
            type="button"
            className="dataset-button"
            disabled={
              !isDatasetVersionId(compareTo) || compareTo === versionId || diffJob.isPending
            }
            onClick={openDiff}
          >
            {diffJob.isPending ? '提交中…' : '创建 Diff Job'}
          </button>
          {diffJob.isError ? <MutationError error={diffJob.error} /> : null}
          {jobId ? <JobStatus jobId={jobId} /> : null}
        </section>
      ) : null}
      {search.tab === 'schema' ? (
        <section className="dataset-band">
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
              <dl className="dataset-metrics">
                <div className="dataset-metric">
                  <dt>Reference</dt>
                  <dd>
                    {schema.data.snapshot.type} / {schema.data.snapshot.id}
                  </dd>
                </div>
                <div className="dataset-metric">
                  <dt>Schema Version</dt>
                  <dd>{schema.data.snapshot.version}</dd>
                </div>
                <div className="dataset-metric">
                  <dt>Channels</dt>
                  <dd>{schema.data.channelCount}</dd>
                </div>
                <div className="dataset-metric">
                  <dt>SHA-256</dt>
                  <dd title={schema.data.snapshot.sha256}>
                    {schema.data.snapshot.sha256.slice(0, 16)}…
                  </dd>
                </div>
              </dl>
              {schema.data.channels.length === 0 ? (
                <RegionState state="empty" />
              ) : (
                <div className="dataset-table-scroll">
                  <table className="dataset-table">
                    <thead>
                      <tr>
                        <th>Channel ID</th>
                        <th>Name</th>
                        <th>Data type</th>
                        <th>Unit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {schema.data.channels.map((channel) => (
                        <tr key={channel.id}>
                          <td>{channel.id}</td>
                          <td>{channel.name}</td>
                          <td>{channel.dataType}</td>
                          <td>{channel.unit ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      ) : null}
      {search.tab === 'capacity' ? (
        <section className="dataset-band">
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
            <div className="dataset-table-scroll">
              <table className="dataset-table">
                <thead>
                  <tr>
                    <th>Object</th>
                    <th>Role</th>
                    <th>Bytes</th>
                    <th>Reuse</th>
                    <th>Protection</th>
                  </tr>
                </thead>
                <tbody>
                  {requiredStorage.data.items.map((item) => (
                    <tr key={item.objectId}>
                      <td>{item.objectId}</td>
                      <td>{item.role}</td>
                      <td>{item.sizeBytes}</td>
                      <td>{item.reuse}</td>
                      <td>{item.protection}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
            <div className="dataset-table-scroll">
              <table className="dataset-table">
                <thead>
                  <tr>
                    <th>Inventory</th>
                    <th>Kind</th>
                    <th>Status</th>
                    <th>Bytes</th>
                    <th>Revision</th>
                  </tr>
                </thead>
                <tbody>
                  {inventory.data.items.map((item) => (
                    <tr key={item.inventoryId}>
                      <td>{item.inventoryId}</td>
                      <td>{item.kind}</td>
                      <td>{item.status}</td>
                      <td>{item.sizeBytes}</td>
                      <td>{item.operationalRevision}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}
      {search.tab === 'exports' ? (
        <section className="dataset-band">
          <h2>导出</h2>
          <p>导出执行状态、结果可用性和下载授权是三个独立状态轴。</p>
          <RegionState
            state="feature-unavailable"
            message="下载授权合同未冻结；页面不会缓存或猜测签名 URL。"
          />
        </section>
      ) : null}

      <ConfirmDialog
        open={approveOpen}
        title="确认复核通过"
        resourceId={versionId}
        description="服务端将创建不可变 APPROVED Decision 并启动发布任务；Job 成功不等同于 Version 已 READY。"
        impact={[
          '创建不可变 ReviewDecision',
          '启动 Manifest/发布异步任务',
          'Version 保持 REVIEWING，直到完整 Bootstrap 证明 READY',
        ]}
        blockedReasons={checks.data?.blockers.map((item) => `${item.code}：${item.message}`)}
        confirmLabel="确认复核通过"
        confirmDisabled={!canApprove}
        submitting={approveMutation.isPending}
        onConfirm={confirmApprove}
        onCancel={() => {
          if (!approveMutation.isPending) {
            setApproveOpen(false);
            setApproveIntentKey(null);
          }
        }}
      />
      <ConfirmDialog
        open={diffOpen}
        title="确认创建版本 Diff Job"
        resourceId={versionId}
        description="异步任务只比较两个固定 Version 的当前内容快照，不会改写 Version 事实。"
        impact={[`比较目标 ${compareTo}`, '创建可审计异步 Job', '任务失败时保留当前页面事实']}
        confirmLabel="确认创建 Diff Job"
        confirmDisabled={!isDatasetVersionId(compareTo) || compareTo === versionId}
        submitting={diffJob.isPending}
        onConfirm={confirmDiff}
        onCancel={() => {
          if (!diffJob.isPending) {
            setDiffOpen(false);
            setDiffIntentKey(null);
          }
        }}
      />
      <ConfirmDialog
        open={returnOpen}
        title="退回固定 Version"
        resourceId={versionId}
        description="服务端将在一次事务中创建不可变 Decision、至少一条 Finding、RETURNED 终态和新 ID 的后继 Draft。"
        impact={[
          '当前 Version 进入终态 RETURNED',
          'Review Decision 与 Finding 永久不可变',
          '创建唯一 successor Draft；前端只使用响应 draftId 跳转',
        ]}
        blockedReasons={returnBlockedReasons}
        confirmLabel="确认原子退回"
        confirmDisabled={!findingValid || !canReturn}
        submitting={returnMutation.isPending}
        onConfirm={confirmReturn}
        onCancel={() => {
          if (!returnMutation.isPending) {
            setReturnOpen(false);
            setReturnIntentKey(null);
          }
        }}
      >
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
      </ConfirmDialog>
    </main>
  );
}

export default VersionDetailPage;
