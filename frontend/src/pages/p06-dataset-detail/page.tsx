import { Button, Card, Tabs, Typography } from 'antd';
import { Eye } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { isDatasetId, type DatasetId } from '../../entities/dataset';
import type { DatasetVersion, DatasetVersionId } from '../../entities/dataset-version';
import type { EpisodeListItemVm } from '../../features/datasets/api';
import {
  useDatasetBootstrapQuery,
  useDatasetVersionCapacityQuery,
  useDatasetVersionSchemaSummaryQuery,
  useDatasetVersionSourceProvenanceQuery,
  useDatasetVersionsQuery,
  useVersionEpisodesQuery,
} from '../../features/datasets/api';
import { routes, type DatasetDetailTab } from '../../features/datasets/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import {
  DetailPageScaffold,
  EntityDrawer,
  FilterToolbar,
  PageState,
  StatusTag,
  UiMetricCard,
  type PageStateKind,
} from '../../shared/ui';
import {
  DatasetCursorPager,
  EpisodeTable,
  SourceTable,
  VersionTable,
} from './components/DatasetDetailTables';
import datasetDetailQueryCodec, { type DatasetDetailSearch } from './query-codec';
import styles from './styles.module.css';

const invalidDataset = 'dataset_invalid' as DatasetId;
const invalidVersion = 'version_invalid' as DatasetVersionId;
const tabs: readonly { id: DatasetDetailTab; label: string }[] = [
  { id: 'overview', label: '概要' },
  { id: 'versions', label: 'Versions' },
  { id: 'episodes', label: 'Episodes' },
  { id: 'schema', label: 'Schema' },
  { id: 'sources', label: '来源' },
  { id: 'capacity', label: '容量' },
];

function pageStateForError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return 'error';
  switch (error.code) {
    case 'FORBIDDEN':
    case 'UNAUTHENTICATED':
      return 'forbidden';
    case 'NOT_FOUND':
      return 'not-found';
    case 'GONE':
      return 'gone';
    case 'VERSION_CONFLICT':
    case 'PRECONDITION_FAILED':
      return 'conflict';
    case 'RATE_LIMITED':
      return 'rate-limited';
    case 'NETWORK_ERROR':
      return 'offline';
    case 'CONTRACT_MISMATCH':
      return 'contract-mismatch';
    default:
      return 'error';
  }
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function VersionFilters({
  search,
  onApply,
}: Readonly<{
  search: DatasetDetailSearch;
  onApply: (changes: Partial<DatasetDetailSearch>) => void;
}>) {
  const [draft, setDraft] = useState(() => ({
    q: search.q ?? '',
    kind: search.versionKind ?? '',
    status: search.versionStatus ?? '',
    sort: search.sort ?? 'created-desc',
    limit: search.limit,
  }));
  useEffect(() => {
    setDraft({
      q: search.q ?? '',
      kind: search.versionKind ?? '',
      status: search.versionStatus ?? '',
      sort: search.sort ?? 'created-desc',
      limit: search.limit,
    });
  }, [search.limit, search.q, search.sort, search.versionKind, search.versionStatus]);
  return (
    <FilterToolbar
      label="版本筛选"
      onApply={() =>
        onApply({
          q: draft.q.trim() || undefined,
          versionKind: (draft.kind || undefined) as DatasetDetailSearch['versionKind'],
          versionStatus: (draft.status || undefined) as DatasetDetailSearch['versionStatus'],
          sort: draft.sort,
          limit: draft.limit,
        })
      }
      onReset={() =>
        onApply({
          q: undefined,
          versionKind: undefined,
          versionStatus: undefined,
          sort: 'created-desc',
          limit: 20,
        })
      }
    >
      <label className={styles.filterField}>
        搜索
        <input
          value={draft.q}
          onChange={(event) => setDraft((value) => ({ ...value, q: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        类型
        <select
          value={draft.kind}
          onChange={(event) => setDraft((value) => ({ ...value, kind: event.target.value }))}
        >
          <option value="">全部</option>
          <option value="raw">RAW</option>
          <option value="cleaned">CLEANED</option>
        </select>
      </label>
      <label className={styles.filterField}>
        状态
        <select
          value={draft.status}
          onChange={(event) => setDraft((value) => ({ ...value, status: event.target.value }))}
        >
          <option value="">全部</option>
          <option value="reviewing">REVIEWING</option>
          <option value="returned">RETURNED</option>
          <option value="ready">READY</option>
        </select>
      </label>
      <label className={styles.filterField}>
        排序
        <select
          value={draft.sort}
          onChange={(event) => setDraft((value) => ({ ...value, sort: event.target.value }))}
        >
          <option value="created-desc">最近创建</option>
          <option value="created-asc">最早创建</option>
          <option value="version-desc">版本降序</option>
          <option value="version-asc">版本升序</option>
        </select>
      </label>
      <label className={styles.filterField}>
        每页
        <select
          value={draft.limit}
          onChange={(event) =>
            setDraft((value) => ({ ...value, limit: Number(event.target.value) as 10 | 20 | 50 }))
          }
        >
          <option value="10">10</option>
          <option value="20">20</option>
          <option value="50">50</option>
        </select>
      </label>
    </FilterToolbar>
  );
}

function EpisodeFilters({
  search,
  onApply,
}: Readonly<{
  search: DatasetDetailSearch;
  onApply: (changes: Partial<DatasetDetailSearch>) => void;
}>) {
  const [draft, setDraft] = useState(() => ({
    q: search.q ?? '',
    task: search.task ?? '',
    success: search.successState ?? '',
    sort: search.sort ?? 'ordinal-asc',
    limit: search.limit,
  }));
  useEffect(
    () =>
      setDraft({
        q: search.q ?? '',
        task: search.task ?? '',
        success: search.successState ?? '',
        sort: search.sort ?? 'ordinal-asc',
        limit: search.limit,
      }),
    [search.limit, search.q, search.sort, search.successState, search.task],
  );
  return (
    <FilterToolbar
      label="Episode 筛选"
      onApply={() =>
        onApply({
          q: draft.q.trim() || undefined,
          task: draft.task.trim() || undefined,
          successState: (draft.success || undefined) as DatasetDetailSearch['successState'],
          sort: draft.sort,
          limit: draft.limit,
        })
      }
      onReset={() =>
        onApply({
          q: undefined,
          task: undefined,
          successState: undefined,
          sort: 'ordinal-asc',
          limit: 20,
        })
      }
    >
      <label className={styles.filterField}>
        搜索
        <input
          value={draft.q}
          onChange={(event) => setDraft((value) => ({ ...value, q: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        任务
        <input
          value={draft.task}
          onChange={(event) => setDraft((value) => ({ ...value, task: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        成功状态
        <select
          value={draft.success}
          onChange={(event) => setDraft((value) => ({ ...value, success: event.target.value }))}
        >
          <option value="">全部</option>
          <option value="succeeded">SUCCEEDED</option>
          <option value="failed">FAILED</option>
          <option value="unknown">UNKNOWN</option>
        </select>
      </label>
      <label className={styles.filterField}>
        排序
        <select
          value={draft.sort}
          onChange={(event) => setDraft((value) => ({ ...value, sort: event.target.value }))}
        >
          <option value="ordinal-asc">Ordinal</option>
          <option value="started-desc">最近开始</option>
          <option value="started-asc">最早开始</option>
        </select>
      </label>
      <label className={styles.filterField}>
        每页
        <select
          value={draft.limit}
          onChange={(event) =>
            setDraft((value) => ({ ...value, limit: Number(event.target.value) as 10 | 20 | 50 }))
          }
        >
          <option value="10">10</option>
          <option value="20">20</option>
          <option value="50">50</option>
        </select>
      </label>
    </FilterToolbar>
  );
}

function SourceFilters({
  search,
  onApply,
}: Readonly<{
  search: DatasetDetailSearch;
  onApply: (changes: Partial<DatasetDetailSearch>) => void;
}>) {
  const [draft, setDraft] = useState(() => ({
    q: search.q ?? '',
    sort: search.sort ?? 'registered-desc',
    limit: search.limit,
  }));
  useEffect(
    () =>
      setDraft({ q: search.q ?? '', sort: search.sort ?? 'registered-desc', limit: search.limit }),
    [search.limit, search.q, search.sort],
  );
  return (
    <FilterToolbar
      label="来源筛选"
      onApply={() =>
        onApply({ q: draft.q.trim() || undefined, sort: draft.sort, limit: draft.limit })
      }
      onReset={() => onApply({ q: undefined, sort: 'registered-desc', limit: 20 })}
    >
      <label className={styles.filterField}>
        搜索
        <input
          value={draft.q}
          onChange={(event) => setDraft((value) => ({ ...value, q: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        排序
        <select
          value={draft.sort}
          onChange={(event) => setDraft((value) => ({ ...value, sort: event.target.value }))}
        >
          <option value="registered-desc">最近注册</option>
          <option value="registered-asc">最早注册</option>
          <option value="source-name-asc">来源名称</option>
        </select>
      </label>
      <label className={styles.filterField}>
        每页
        <select
          value={draft.limit}
          onChange={(event) =>
            setDraft((value) => ({ ...value, limit: Number(event.target.value) as 10 | 20 | 50 }))
          }
        >
          <option value="10">10</option>
          <option value="20">20</option>
          <option value="50">50</option>
        </select>
      </label>
    </FilterToolbar>
  );
}

export function DatasetDetailPage() {
  const { datasetId: rawDatasetId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [compactInspector, setCompactInspector] = useState(() => globalThis.innerWidth <= 1024);
  const search = datasetDetailQueryCodec.parse(params);
  const capabilities = useCapabilities();
  const valid = isDatasetId(rawDatasetId);
  const datasetId = valid ? rawDatasetId : invalidDataset;
  const bootstrap = useDatasetBootstrapQuery(datasetId, valid && capabilities.has('dataset.read'));
  const chosenVersionId =
    search.versionId ??
    bootstrap.data?.suggestedVersionId ??
    bootstrap.data?.currentReadyVersion?.versionId ??
    invalidVersion;
  const hasChosenVersion = chosenVersionId !== invalidVersion;
  const versions = useDatasetVersionsQuery(
    datasetId,
    search,
    valid && search.tab === 'versions' && capabilities.has('dataset_version.read'),
  );
  const episodes = useVersionEpisodesQuery(
    datasetId,
    chosenVersionId,
    search,
    valid && search.tab === 'episodes' && hasChosenVersion && capabilities.has('episode.read'),
  );
  const schema = useDatasetVersionSchemaSummaryQuery(
    datasetId,
    chosenVersionId,
    valid && search.tab === 'schema' && hasChosenVersion && capabilities.has('data_schema.read'),
  );
  const sources = useDatasetVersionSourceProvenanceQuery(
    datasetId,
    chosenVersionId,
    search,
    valid &&
      search.tab === 'sources' &&
      hasChosenVersion &&
      capabilities.has('dataset_version.read'),
  );
  const capacity = useDatasetVersionCapacityQuery(
    datasetId,
    chosenVersionId,
    valid &&
      search.tab === 'capacity' &&
      hasChosenVersion &&
      capabilities.has('storage.overview.read'),
  );
  const versionBoundTab = ['episodes', 'schema', 'sources', 'capacity'].includes(search.tab);

  useEffect(() => {
    const media = globalThis.matchMedia('(max-width: 1024px)');
    const update = () => setCompactInspector(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  const applySearch = (changes: Partial<DatasetDetailSearch>, replace = false) => {
    const next = datasetDetailQueryCodec.withChanges(search, changes);
    const serialized = datasetDetailQueryCodec.build(next).toString();
    const base = routes.datasetDetail.build({ datasetId });
    void navigate(serialized ? `${base}?${serialized}` : base, { replace });
  };

  useEffect(() => {
    if (!valid || !versionBoundTab || search.versionId || !hasChosenVersion) return;
    const next = datasetDetailQueryCodec.withChanges(search, { versionId: chosenVersionId });
    const serialized = datasetDetailQueryCodec.build(next).toString();
    const base = routes.datasetDetail.build({ datasetId });
    void navigate(serialized ? `${base}?${serialized}` : base, { replace: true });
  }, [
    chosenVersionId,
    datasetId,
    hasChosenVersion,
    navigate,
    search,
    search.versionId,
    valid,
    versionBoundTab,
  ]);

  useEffect(() => {
    if (
      !episodes.isSuccess ||
      !search.episodeId ||
      episodes.data.items.some((item) => item.episodeId === search.episodeId)
    )
      return;
    const next = datasetDetailQueryCodec.withChanges(search, { episodeId: undefined });
    const serialized = datasetDetailQueryCodec.build(next).toString();
    const base = routes.datasetDetail.build({ datasetId });
    void navigate(serialized ? `${base}?${serialized}` : base, { replace: true });
  }, [datasetId, episodes.data, episodes.isSuccess, navigate, search, search.episodeId]);

  if (!valid)
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState
          state="not-found"
          title="Dataset ID 格式无效"
          description="必须使用稳定、不可变的 Dataset ID。"
        />
      </main>
    );
  if (capabilities.loading || bootstrap.isPending)
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState state="loading" label="数据集详情" />
      </main>
    );
  if (capabilities.failed || !capabilities.has('dataset.read'))
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState state="forbidden" />
      </main>
    );
  if (bootstrap.isError)
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState
          state={pageStateForError(bootstrap.error)}
          requestId={requestId(bootstrap.error)}
          onRetry={() => void bootstrap.refetch()}
        />
      </main>
    );

  const data = bootstrap.data;
  const selectedEpisode = episodes.data?.items.find((item) => item.episodeId === search.episodeId);
  const openViewer = (episode: EpisodeListItemVm) =>
    void navigate(
      routes.episodeViewer.build({
        datasetId,
        versionId: episode.versionId,
        episodeId: episode.episodeId,
        returnTo: `${location.pathname}${location.search}`,
      }),
    );
  const tabContent = (() => {
    if (search.tab === 'overview')
      return (
        <div className={styles.stack}>
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <div>
                <Typography.Title level={2}>概要</Typography.Title>
                <Typography.Paragraph>
                  所有统计来自同一授权聚合；未知值不做推断。
                </Typography.Paragraph>
              </div>
              <StatusTag
                status={data.dataset.availability}
                tone={data.dataset.availability === 'ACTIVE' ? 'success' : 'warning'}
                known={data.dataset.availability !== 'UNKNOWN'}
              />
            </div>
            <div className={styles.metricGrid}>
              <UiMetricCard label="Episodes" value={data.summary.episodeCount} basis="授权聚合" />
              <UiMetricCard
                label="有效时长"
                value={data.summary.effectiveDurationNs}
                unit="ns"
                basis="授权聚合"
              />
              <UiMetricCard label="源字节" value={data.summary.sourceBytes} basis="授权聚合" />
              <UiMetricCard
                label="必需物理字节"
                value={data.summary.requiredPhysicalBytes}
                basis="授权聚合"
              />
              <UiMetricCard
                label="实际 OSS"
                value={data.summary.actualOssBytes}
                state={data.summary.actualOssBytes === null ? 'unknown' : 'ready'}
                basis="容量事实"
              />
              <UiMetricCard label="待复核" value={data.summary.pendingReviewVersionCount} />
              <UiMetricCard label="已退回" value={data.summary.returnedVersionCount} />
              <UiMetricCard
                label="可处理草稿"
                value={data.summary.actionableDraftCount}
                asOf={new Date(data.summary.calculatedAt).toLocaleString()}
              />
            </div>
          </section>
          <Card
            title="当前 Ready 版本"
            extra={
              data.currentReadyVersion ? (
                <Button
                  type="primary"
                  onClick={() =>
                    void navigate(
                      routes.versionDetail.build({
                        datasetId,
                        versionId: data.currentReadyVersion!.versionId,
                        returnTo: `${location.pathname}${location.search}`,
                      }),
                    )
                  }
                >
                  打开 {data.currentReadyVersion.displayVersion}
                </Button>
              ) : null
            }
          >
            {data.currentReadyVersion ? (
              <Typography.Paragraph>
                <code>{data.currentReadyVersion.versionId}</code> · Manifest{' '}
                {data.currentReadyVersion.manifestSha256.slice(0, 12)}…
              </Typography.Paragraph>
            ) : (
              <PageState state="empty" description="该数据集尚无 Ready 版本；没有创建伪造版本。" />
            )}
          </Card>
        </div>
      );
    if (search.tab === 'versions')
      return (
        <section className={styles.section}>
          <div className={styles.sectionHeader}>
            <div>
              <Typography.Title level={2}>Versions</Typography.Title>
              <Typography.Paragraph>
                Version ID 稳定且不可变；不接受 latest/current。
              </Typography.Paragraph>
            </div>
          </div>
          <VersionFilters search={search} onApply={applySearch} />
          {versions.isPending ? (
            <PageState state="loading" label="版本列表" />
          ) : versions.isError ? (
            <PageState
              state={pageStateForError(versions.error)}
              requestId={requestId(versions.error)}
              onRetry={() => void versions.refetch()}
            />
          ) : versions.data.items.length === 0 ? (
            <PageState state="filtered-empty" />
          ) : (
            <>
              <VersionTable
                items={versions.data.items}
                onOpen={(version: DatasetVersion) =>
                  void navigate(
                    routes.versionDetail.build({
                      datasetId,
                      versionId: version.id,
                      tab: version.status === 'REVIEWING' ? 'review' : 'revisions',
                      returnTo: `${location.pathname}${location.search}`,
                    }),
                  )
                }
              />
              <DatasetCursorPager
                page={versions.data}
                busy={versions.isFetching}
                onChange={applySearch}
              />
            </>
          )}
        </section>
      );
    if (search.tab === 'episodes')
      return (
        <section className={styles.section}>
          <div className={styles.sectionHeader}>
            <div>
              <Typography.Title level={2}>Episodes</Typography.Title>
              <Typography.Paragraph>
                固定 Dataset + Version + Episode 身份进入只读 Viewer。
              </Typography.Paragraph>
            </div>
          </div>
          {!hasChosenVersion ? (
            <PageState state="empty" description="没有可用于列出 Episode 的固定版本。" />
          ) : (
            <div className={styles.episodeWorkspace}>
              <aside className={styles.filterPanel} aria-label="Episode 筛选面板">
                <EpisodeFilters search={search} onApply={applySearch} />
              </aside>
              <div className={styles.tablePanel}>
                {episodes.isPending ? (
                  <PageState state="loading" label="Episode 列表" />
                ) : episodes.isError ? (
                  <PageState
                    state={pageStateForError(episodes.error)}
                    requestId={requestId(episodes.error)}
                    onRetry={() => void episodes.refetch()}
                  />
                ) : episodes.data.items.length === 0 ? (
                  <PageState state="filtered-empty" />
                ) : (
                  <>
                    <EpisodeTable
                      items={episodes.data.items}
                      onInspect={(episode) => applySearch({ episodeId: episode.episodeId })}
                      onOpenViewer={openViewer}
                    />
                    <DatasetCursorPager
                      page={episodes.data}
                      busy={episodes.isFetching}
                      onChange={(cursor) => applySearch({ ...cursor, episodeId: undefined })}
                    />
                    <section
                      className={styles.episodeWindowSummary}
                      aria-label="当前 Episode 数据窗口"
                    >
                      <div>
                        <span>当前窗口</span>
                        <strong>{episodes.data.items.length} 条</strong>
                      </div>
                      <div>
                        <span>固定 Version</span>
                        <code title={chosenVersionId}>{chosenVersionId}</code>
                      </div>
                      <div>
                        <span>快照时间</span>
                        <time dateTime={episodes.data.snapshotAt}>
                          {new Date(episodes.data.snapshotAt).toLocaleString()}
                        </time>
                      </div>
                      <p>仅展示当前授权快照中的真实记录，不以推测行填充稀疏窗口。</p>
                    </section>
                  </>
                )}
              </div>
            </div>
          )}
        </section>
      );
    if (search.tab === 'schema')
      return !capabilities.has('data_schema.read') ? (
        <PageState state="forbidden" description="Schema 摘要需要 data_schema.read。" />
      ) : !hasChosenVersion ? (
        <PageState state="empty" />
      ) : schema.isPending ? (
        <PageState state="loading" label="Schema" />
      ) : schema.isError ? (
        <PageState
          state={pageStateForError(schema.error)}
          requestId={requestId(schema.error)}
          onRetry={() => void schema.refetch()}
        />
      ) : (
        <section className={styles.section}>
          <Typography.Title level={2}>Schema Snapshot</Typography.Title>
          <div className={styles.metricGrid}>
            <UiMetricCard label="Snapshot" value={schema.data.snapshot.id} />
            <UiMetricCard label="Version" value={schema.data.snapshot.version} />
            <UiMetricCard
              label="Channels"
              value={schema.data.channelCount}
              state={schema.data.channelCount === null ? 'unknown' : 'ready'}
            />
          </div>
          <Typography.Paragraph>
            SHA-256 <code>{schema.data.snapshot.sha256}</code>
          </Typography.Paragraph>
        </section>
      );
    if (search.tab === 'sources')
      return !hasChosenVersion ? (
        <PageState state="empty" />
      ) : sources.isPending ? (
        <PageState state="loading" label="来源证据" />
      ) : sources.isError ? (
        <PageState
          state={pageStateForError(sources.error)}
          requestId={requestId(sources.error)}
          onRetry={() => void sources.refetch()}
        />
      ) : (
        <section className={styles.section}>
          <Typography.Title level={2}>安全来源证据</Typography.Title>
          <SourceFilters search={search} onApply={applySearch} />
          {sources.data.items.length === 0 ? (
            <PageState state="filtered-empty" />
          ) : (
            <>
              <SourceTable items={sources.data.items} />
              <DatasetCursorPager
                page={sources.data}
                busy={sources.isFetching}
                onChange={applySearch}
              />
            </>
          )}
        </section>
      );
    return !capabilities.has('storage.overview.read') ? (
      <PageState state="forbidden" description="容量事实需要 storage.overview.read。" />
    ) : !hasChosenVersion ? (
      <PageState state="empty" />
    ) : capacity.isPending ? (
      <PageState state="loading" label="容量事实" />
    ) : capacity.isError ? (
      <PageState
        state={pageStateForError(capacity.error)}
        requestId={requestId(capacity.error)}
        onRetry={() => void capacity.refetch()}
      />
    ) : (
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <Typography.Title level={2}>容量事实</Typography.Title>
          <StatusTag
            status={capacity.data.state}
            tone={capacity.data.state === 'SETTLED' ? 'success' : 'warning'}
            known
          />
        </div>
        {capacity.data.state === 'PARTIAL' || capacity.data.state === 'FAILED' ? (
          <PageState
            state="error"
            title="容量事实不完整"
            description="未知值保持为未知，不显示 0。"
            onRetry={() => void capacity.refetch()}
          />
        ) : null}
        <div className={styles.metricGrid}>
          <UiMetricCard
            label="源字节"
            value={capacity.data.sourceBytes}
            state={capacity.data.sourceBytes === null ? 'unknown' : 'ready'}
          />
          <UiMetricCard
            label="必需物理字节"
            value={capacity.data.requiredPhysicalBytes}
            state={capacity.data.requiredPhysicalBytes === null ? 'unknown' : 'ready'}
          />
          <UiMetricCard
            label="实际 OSS 字节"
            value={capacity.data.actualOssBytes}
            state={capacity.data.actualOssBytes === null ? 'unknown' : 'ready'}
          />
        </div>
        <Typography.Paragraph>
          Basis <code>{capacity.data.basisRevision}</code> ·{' '}
          {new Date(capacity.data.calculatedAt).toLocaleString()}
        </Typography.Paragraph>
      </section>
    );
  })();

  return (
    <main className={styles.page} data-page-id="P06">
      <DetailPageScaffold
        resourceId={datasetId}
        header={{
          title: data.dataset.name,
          description: data.dataset.description || '暂无描述',
          breadcrumbs: [
            { key: 'assets', label: '数据资产' },
            { key: 'datasets', label: '数据集' },
            { key: datasetId, label: data.dataset.name },
          ],
          actions: search.returnTo ? (
            <Button onClick={() => void navigate(search.returnTo!)}>返回列表</Button>
          ) : undefined,
        }}
        tabs={
          <div className={styles.tabBand}>
            <div className={styles.summaryBar}>
              <div>
                <span>Ready 版本</span>
                <strong>{data.currentReadyVersion?.displayVersion ?? '—'}</strong>
              </div>
              <div>
                <span>Episodes</span>
                <strong>{data.summary.episodeCount}</strong>
              </div>
              <div>
                <span>有效时长</span>
                <strong>{data.summary.effectiveDurationNs} ns</strong>
              </div>
              <div>
                <span>源字节</span>
                <strong>{data.summary.sourceBytes}</strong>
              </div>
              <div>
                <span>实际 OSS</span>
                <strong>{data.summary.actualOssBytes ?? '未知'}</strong>
              </div>
            </div>
            <Tabs
              activeKey={search.tab}
              items={tabs.map((tab) => ({ key: tab.id, label: tab.label }))}
              more={{
                icon: (
                  <>
                    <span aria-hidden="true">•••</span>
                    <span className={styles.srOnly}>更多数据集详情标签页</span>
                  </>
                ),
              }}
              onChange={(key) =>
                applySearch({
                  tab: key as DatasetDetailTab,
                  versionId:
                    ['episodes', 'schema', 'sources', 'capacity'].includes(key) && hasChosenVersion
                      ? chosenVersionId
                      : undefined,
                })
              }
            />
          </div>
        }
        inspector={
          !compactInspector && search.tab === 'episodes' ? (
            selectedEpisode ? (
              <EpisodeInspector episode={selectedEpisode} onOpenViewer={openViewer} />
            ) : (
              <PageState
                state="empty"
                title="未选择 Episode"
                description="选择表格中的 Episode 查看稳定身份与快捷操作。"
              />
            )
          ) : undefined
        }
        inspectorLabel="选中 Episode"
      >
        {tabContent}
      </DetailPageScaffold>
      <EntityDrawer
        open={Boolean(selectedEpisode) && compactInspector}
        title={<Typography.Title level={2}>Episode Inspector</Typography.Title>}
        onClose={() => applySearch({ episodeId: undefined })}
      >
        {selectedEpisode ? (
          <EpisodeInspector episode={selectedEpisode} onOpenViewer={openViewer} />
        ) : null}
      </EntityDrawer>
    </main>
  );
}

function EpisodeInspector({
  episode,
  onOpenViewer,
}: Readonly<{ episode: EpisodeListItemVm; onOpenViewer: (episode: EpisodeListItemVm) => void }>) {
  return (
    <div className={styles.drawerBody}>
      <header className={styles.inspectorHero}>
        <span className={styles.episodeOrdinal}>#{episode.ordinal + 1}</span>
        <div>
          <span>选中 Episode</span>
          <Typography.Title level={3}>{episode.episodeId}</Typography.Title>
        </div>
        <StatusTag
          status={episode.successState}
          tone={
            episode.successState === 'SUCCEEDED'
              ? 'success'
              : episode.successState === 'FAILED'
                ? 'danger'
                : 'warning'
          }
          known={episode.successState !== 'UNKNOWN'}
        />
      </header>
      <dl>
        <dt>Revision</dt>
        <dd>
          <code>{episode.selectedRevisionId}</code>
        </dd>
        <dt>Version</dt>
        <dd>
          <code>{episode.versionId}</code>
        </dd>
        <dt>任务</dt>
        <dd>{episode.task ?? '—'}</dd>
        <dt>机器人</dt>
        <dd>
          <code>{episode.robotId ?? '—'}</code>
        </dd>
        <dt>Included</dt>
        <dd>{episode.included ? '是' : '否'}</dd>
        <dt>复核投影</dt>
        <dd>
          {episode.reviewStatus} · {episode.reviewFindingCount}
        </dd>
      </dl>
      <div className={styles.drawerActions}>
        <Button type="primary" icon={<Eye size={16} />} block onClick={() => onOpenViewer(episode)}>
          打开只读 Viewer
        </Button>
      </div>
    </div>
  );
}

export default DatasetDetailPage;
