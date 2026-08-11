import { useEffect } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { isDatasetId, type DatasetId } from '../../entities/dataset';
import type { DatasetVersionId } from '../../entities/dataset-version';
import {
  useDatasetBootstrapQuery,
  useDatasetVersionCapacityQuery,
  useDatasetVersionSchemaSummaryQuery,
  useDatasetVersionSourceProvenanceQuery,
  useDatasetVersionsQuery,
  useVersionEpisodesQuery,
} from '../../features/datasets/api';
import { CursorPager } from '../../features/datasets/components/CursorPager';
import { RegionState } from '../../features/datasets/components/RegionState';
import { datasetRegionStateForError } from '../../features/datasets/components/error-state';
import { routes, type DatasetDetailTab } from '../../features/datasets/routing';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import datasetDetailQueryCodec, { type DatasetDetailSearch } from './query-codec';
import '../../features/datasets/components/datasets.css';

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

function formText(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === 'string' ? value : '';
}

export function DatasetDetailPage() {
  const { datasetId: rawDatasetId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
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
  const versionBoundTab =
    search.tab === 'episodes' ||
    search.tab === 'schema' ||
    search.tab === 'sources' ||
    search.tab === 'capacity';

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
    if (!episodes.isSuccess || !search.episodeId) return;
    if (!episodes.data.items.some((item) => item.episodeId === search.episodeId)) {
      const next = datasetDetailQueryCodec.withChanges(search, { episodeId: undefined });
      const serialized = datasetDetailQueryCodec.build(next).toString();
      const base = routes.datasetDetail.build({ datasetId });
      void navigate(serialized ? `${base}?${serialized}` : base, { replace: true });
    }
  }, [datasetId, episodes.data, episodes.isSuccess, navigate, search]);

  if (!valid)
    return (
      <main className="dataset-page" data-page-id="P06">
        <RegionState state="not-found" message="Dataset ID 格式无效。" />
      </main>
    );
  if (capabilities.loading || bootstrap.isPending)
    return (
      <main className="dataset-page">
        <RegionState state="first-loading" />
      </main>
    );
  if (capabilities.failed || !capabilities.has('dataset.read'))
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
  const selectedEpisode = episodes.data?.items.find((item) => item.episodeId === search.episodeId);
  const changeTab = (tab: DatasetDetailTab) =>
    applySearch({
      tab,
      versionId:
        (tab === 'episodes' || tab === 'schema' || tab === 'sources' || tab === 'capacity') &&
        hasChosenVersion
          ? chosenVersionId
          : undefined,
    });

  return (
    <main className="dataset-page" data-page-id="P06">
      <header className="dataset-resource-header">
        <div>
          <p className="dataset-eyebrow">Dataset detail</p>
          <h1>{data.dataset.name}</h1>
          <p>
            <code>{datasetId}</code> · {data.dataset.description || '暂无描述'}
          </p>
        </div>
        <div className="dataset-actions">
          {search.returnTo ? (
            <button
              type="button"
              className="dataset-button dataset-button--secondary"
              onClick={() => {
                void navigate(search.returnTo!);
              }}
            >
              返回列表
            </button>
          ) : null}
        </div>
      </header>
      <nav className="dataset-tabs" aria-label="数据集详情分区">
        {tabs.map((tab) => (
          <button
            type="button"
            className="dataset-tab"
            role="tab"
            aria-selected={search.tab === tab.id}
            key={tab.id}
            onClick={() => changeTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {search.tab === 'overview' ? (
        <>
          <section className="dataset-band">
            <div className="dataset-band-heading">
              <div>
                <h2>概要</h2>
                <p>所有统计来自同一授权聚合；未知值不做推断。</p>
              </div>
              <span
                className={`dataset-status dataset-status--${data.dataset.availability.toLowerCase()}`}
              >
                {data.dataset.availability}
              </span>
            </div>
            <dl className="dataset-metrics">
              <div className="dataset-metric">
                <dt>Episodes</dt>
                <dd>{data.summary.episodeCount}</dd>
              </div>
              <div className="dataset-metric">
                <dt>有效时长(ns)</dt>
                <dd>{data.summary.effectiveDurationNs}</dd>
              </div>
              <div className="dataset-metric">
                <dt>源字节</dt>
                <dd>{data.summary.sourceBytes}</dd>
              </div>
              <div className="dataset-metric">
                <dt>必需物理字节</dt>
                <dd>{data.summary.requiredPhysicalBytes}</dd>
              </div>
              <div className="dataset-metric">
                <dt>实际 OSS</dt>
                <dd>{data.summary.actualOssBytes ?? '未知'}</dd>
              </div>
              <div className="dataset-metric">
                <dt>待复核</dt>
                <dd>{data.summary.pendingReviewVersionCount}</dd>
              </div>
              <div className="dataset-metric">
                <dt>已退回</dt>
                <dd>{data.summary.returnedVersionCount}</dd>
              </div>
              <div className="dataset-metric">
                <dt>可处理草稿</dt>
                <dd>{data.summary.actionableDraftCount}</dd>
              </div>
            </dl>
            <p>
              统计状态 {data.summary.calculationState} ·{' '}
              {new Date(data.summary.calculatedAt).toLocaleString()}
            </p>
          </section>
          <section className="dataset-band">
            <div className="dataset-band-heading">
              <div>
                <h2>当前 Ready 版本</h2>
                <p>只使用服务端返回的固定 versionId。</p>
              </div>
              {data.currentReadyVersion ? (
                <button
                  type="button"
                  className="dataset-button"
                  onClick={() => {
                    void navigate(
                      routes.versionDetail.build({
                        datasetId,
                        versionId: data.currentReadyVersion!.versionId,
                        returnTo: `${location.pathname}${location.search}`,
                      }),
                    );
                  }}
                >
                  打开 {data.currentReadyVersion.displayVersion}
                </button>
              ) : null}
            </div>
            {!data.currentReadyVersion ? (
              <RegionState state="empty" message="该数据集尚无 Ready 版本；没有创建伪造版本。" />
            ) : (
              <p>
                <code>{data.currentReadyVersion.versionId}</code> · Manifest{' '}
                {data.currentReadyVersion.manifestSha256.slice(0, 12)}…
              </p>
            )}
          </section>
        </>
      ) : null}

      {search.tab === 'versions' ? (
        <section className="dataset-band">
          <div className="dataset-band-heading">
            <div>
              <h2>Versions</h2>
              <p>Version ID 稳定且不可变；不接受 latest/current。</p>
            </div>
          </div>
          <form
            className="dataset-inline-filters"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              applySearch({
                q: formText(form, 'q').trim() || undefined,
                versionKind: (formText(form, 'kind') ||
                  undefined) as DatasetDetailSearch['versionKind'],
                versionStatus: (formText(form, 'status') ||
                  undefined) as DatasetDetailSearch['versionStatus'],
                sort: formText(form, 'sort'),
                limit: Number(formText(form, 'limit')) as DatasetDetailSearch['limit'],
              });
            }}
          >
            <label>
              搜索
              <input name="q" defaultValue={search.q} />
            </label>
            <label>
              类型
              <select name="kind" defaultValue={search.versionKind ?? ''}>
                <option value="">全部</option>
                <option value="raw">RAW</option>
                <option value="cleaned">CLEANED</option>
              </select>
            </label>
            <label>
              状态
              <select name="status" defaultValue={search.versionStatus ?? ''}>
                <option value="">全部</option>
                <option value="reviewing">REVIEWING</option>
                <option value="returned">RETURNED</option>
                <option value="ready">READY</option>
              </select>
            </label>
            <label>
              排序
              <select name="sort" defaultValue={search.sort}>
                <option value="created-desc">最近创建</option>
                <option value="created-asc">最早创建</option>
                <option value="version-desc">版本降序</option>
                <option value="version-asc">版本升序</option>
              </select>
            </label>
            <label>
              每页
              <select name="limit" defaultValue={search.limit}>
                <option value="10">10</option>
                <option value="20">20</option>
                <option value="50">50</option>
              </select>
            </label>
            <button type="submit" className="dataset-button">
              应用
            </button>
          </form>
          {versions.isPending ? (
            <RegionState state="first-loading" />
          ) : versions.isError ? (
            <RegionState
              state={datasetRegionStateForError(versions.error)}
              onRetry={() => void versions.refetch()}
            />
          ) : versions.data.items.length === 0 ? (
            <RegionState state="empty" />
          ) : (
            <>
              <div className="dataset-table-scroll">
                <table className="dataset-table">
                  <thead>
                    <tr>
                      <th>版本</th>
                      <th>类型</th>
                      <th>状态</th>
                      <th>创建时间</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {versions.data.items.map((version) => (
                      <tr key={version.id}>
                        <td>
                          <strong>{version.displayVersion}</strong>
                          <small>{version.id}</small>
                        </td>
                        <td>{version.kind}</td>
                        <td>
                          <span
                            className={`dataset-status dataset-status--${version.status.toLowerCase()}`}
                          >
                            {version.status}
                          </span>
                        </td>
                        <td>{new Date(version.createdAt).toLocaleString()}</td>
                        <td>
                          <button
                            type="button"
                            className="dataset-link"
                            onClick={() => {
                              void navigate(
                                routes.versionDetail.build({
                                  datasetId,
                                  versionId: version.id,
                                  tab: version.status === 'REVIEWING' ? 'review' : 'revisions',
                                  returnTo: `${location.pathname}${location.search}`,
                                }),
                              );
                            }}
                          >
                            打开
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <CursorPager
                pageInfo={versions.data.pageInfo}
                snapshotAt={versions.data.snapshotAt}
                onPrevious={() =>
                  applySearch({
                    before: versions.data.pageInfo.before ?? undefined,
                    after: undefined,
                  })
                }
                onNext={() =>
                  applySearch({
                    after: versions.data.pageInfo.after ?? undefined,
                    before: undefined,
                  })
                }
              />
            </>
          )}
        </section>
      ) : null}

      {search.tab === 'episodes' ? (
        <section className="dataset-band">
          <div className="dataset-band-heading">
            <div>
              <h2>Episodes</h2>
              <p>固定 Dataset + Version + Episode 身份进入 P06 隐藏 Viewer。</p>
            </div>
          </div>
          {!hasChosenVersion ? (
            <RegionState state="empty" message="没有可用于列出 Episode 的固定版本。" />
          ) : (
            <>
              <form
                className="dataset-inline-filters"
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = new FormData(event.currentTarget);
                  applySearch({
                    q: formText(form, 'q').trim() || undefined,
                    task: formText(form, 'task').trim() || undefined,
                    successState: (formText(form, 'success') ||
                      undefined) as DatasetDetailSearch['successState'],
                    sort: formText(form, 'sort'),
                    limit: Number(formText(form, 'limit')) as DatasetDetailSearch['limit'],
                  });
                }}
              >
                <label>
                  搜索
                  <input name="q" defaultValue={search.q} />
                </label>
                <label>
                  任务
                  <input name="task" defaultValue={search.task} />
                </label>
                <label>
                  成功状态
                  <select name="success" defaultValue={search.successState ?? ''}>
                    <option value="">全部</option>
                    <option value="succeeded">SUCCEEDED</option>
                    <option value="failed">FAILED</option>
                    <option value="unknown">UNKNOWN</option>
                  </select>
                </label>
                <label>
                  排序
                  <select name="sort" defaultValue={search.sort}>
                    <option value="ordinal-asc">Ordinal</option>
                    <option value="started-desc">最近开始</option>
                    <option value="started-asc">最早开始</option>
                  </select>
                </label>
                <label>
                  每页
                  <select name="limit" defaultValue={search.limit}>
                    <option value="10">10</option>
                    <option value="20">20</option>
                    <option value="50">50</option>
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
                          <th>任务</th>
                          <th>成功状态</th>
                          <th>复核投影</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {episodes.data.items.map((episode) => (
                          <tr key={episode.episodeId}>
                            <td>
                              <button
                                type="button"
                                className="dataset-link"
                                onClick={() => applySearch({ episodeId: episode.episodeId })}
                              >
                                <strong>#{episode.ordinal + 1}</strong>
                                <small>{episode.episodeId}</small>
                              </button>
                            </td>
                            <td>
                              <code>{episode.selectedRevisionId}</code>
                            </td>
                            <td>{episode.task ?? '—'}</td>
                            <td>{episode.successState}</td>
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
                                      versionId: episode.versionId,
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
                  <CursorPager
                    pageInfo={episodes.data.pageInfo}
                    snapshotAt={episodes.data.snapshotAt}
                    onPrevious={() =>
                      applySearch({
                        before: episodes.data.pageInfo.before ?? undefined,
                        after: undefined,
                        episodeId: undefined,
                      })
                    }
                    onNext={() =>
                      applySearch({
                        after: episodes.data.pageInfo.after ?? undefined,
                        before: undefined,
                        episodeId: undefined,
                      })
                    }
                  />
                </>
              )}
              {selectedEpisode ? (
                <aside className="dataset-inspector" aria-label="Episode 详情">
                  <div className="dataset-band-heading">
                    <h3>Episode Inspector</h3>
                    <button
                      type="button"
                      className="dataset-link"
                      onClick={() => applySearch({ episodeId: undefined })}
                    >
                      关闭
                    </button>
                  </div>
                  <dl>
                    <dt>Episode</dt>
                    <dd>
                      <code>{selectedEpisode.episodeId}</code>
                    </dd>
                    <dt>Revision</dt>
                    <dd>
                      <code>{selectedEpisode.selectedRevisionId}</code>
                    </dd>
                    <dt>Version</dt>
                    <dd>
                      <code>{selectedEpisode.versionId}</code>
                    </dd>
                  </dl>
                  <button
                    type="button"
                    className="dataset-button"
                    onClick={() => {
                      void navigate(
                        routes.episodeViewer.build({
                          datasetId,
                          versionId: selectedEpisode.versionId,
                          episodeId: selectedEpisode.episodeId,
                          returnTo: `${location.pathname}${location.search}`,
                        }),
                      );
                    }}
                  >
                    打开只读 Viewer
                  </button>
                </aside>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      {search.tab === 'schema' ? (
        !capabilities.has('data_schema.read') ? (
          <RegionState state="forbidden" message="Schema 摘要需要 data_schema.read。" />
        ) : !hasChosenVersion ? (
          <RegionState state="empty" />
        ) : schema.isPending ? (
          <RegionState state="first-loading" />
        ) : schema.isError ? (
          <RegionState
            state={datasetRegionStateForError(schema.error)}
            onRetry={() => void schema.refetch()}
          />
        ) : (
          <section className="dataset-band">
            <h2>Schema Snapshot</h2>
            <dl className="dataset-metrics">
              <div className="dataset-metric">
                <dt>Snapshot</dt>
                <dd>{schema.data.snapshot.id}</dd>
              </div>
              <div className="dataset-metric">
                <dt>Version</dt>
                <dd>{schema.data.snapshot.version}</dd>
              </div>
              <div className="dataset-metric">
                <dt>Channels</dt>
                <dd>{schema.data.channelCount ?? '未知'}</dd>
              </div>
            </dl>
            <p>
              SHA-256 <code>{schema.data.snapshot.sha256}</code>
            </p>
          </section>
        )
      ) : null}

      {search.tab === 'sources' ? (
        !hasChosenVersion ? (
          <RegionState state="empty" />
        ) : sources.isPending ? (
          <RegionState state="first-loading" />
        ) : sources.isError ? (
          <RegionState
            state={datasetRegionStateForError(sources.error)}
            onRetry={() => void sources.refetch()}
          />
        ) : (
          <section className="dataset-band">
            <h2>安全来源证据</h2>
            <form
              className="dataset-inline-filters"
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                applySearch({
                  q: formText(form, 'q').trim() || undefined,
                  sort: formText(form, 'sort'),
                  limit: Number(formText(form, 'limit')) as DatasetDetailSearch['limit'],
                });
              }}
            >
              <label>
                搜索
                <input name="q" defaultValue={search.q} />
              </label>
              <label>
                排序
                <select name="sort" defaultValue={search.sort}>
                  <option value="registered-desc">最近注册</option>
                  <option value="registered-asc">最早注册</option>
                  <option value="source-name-asc">来源名称</option>
                </select>
              </label>
              <label>
                每页
                <select name="limit" defaultValue={search.limit}>
                  <option value="10">10</option>
                  <option value="20">20</option>
                  <option value="50">50</option>
                </select>
              </label>
              <button type="submit" className="dataset-button">
                应用
              </button>
            </form>
            {sources.data.items.length === 0 ? (
              <RegionState state="empty" />
            ) : (
              <>
                <div className="dataset-table-scroll">
                  <table className="dataset-table">
                    <thead>
                      <tr>
                        <th>来源</th>
                        <th>Upload</th>
                        <th>Manifest</th>
                        <th>注册时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sources.data.items.map((item) => (
                        <tr key={item.provenanceId}>
                          <td>
                            {item.sourceDisplayName ?? '已脱敏'}
                            <small>{item.sourceId ?? '—'}</small>
                          </td>
                          <td>
                            <code>{item.uploadId}</code>
                          </td>
                          <td>
                            <code>{item.sourceManifestId}</code>
                          </td>
                          <td>{new Date(item.registeredAt).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <CursorPager
                  pageInfo={sources.data.pageInfo}
                  snapshotAt={sources.data.snapshotAt}
                  onPrevious={() =>
                    applySearch({
                      before: sources.data.pageInfo.before ?? undefined,
                      after: undefined,
                    })
                  }
                  onNext={() =>
                    applySearch({
                      after: sources.data.pageInfo.after ?? undefined,
                      before: undefined,
                    })
                  }
                />
              </>
            )}
          </section>
        )
      ) : null}

      {search.tab === 'capacity' ? (
        !capabilities.has('storage.overview.read') ? (
          <RegionState state="forbidden" message="容量事实需要 storage.overview.read。" />
        ) : !hasChosenVersion ? (
          <RegionState state="empty" />
        ) : capacity.isPending ? (
          <RegionState state="first-loading" />
        ) : capacity.isError ? (
          <RegionState
            state={datasetRegionStateForError(capacity.error)}
            onRetry={() => void capacity.refetch()}
          />
        ) : (
          <section className="dataset-band">
            <h2>容量事实</h2>
            {capacity.data.state === 'PARTIAL' || capacity.data.state === 'FAILED' ? (
              <RegionState
                state="partial-error"
                message="容量事实不完整；未知值保持为未知，不显示 0。"
                onRetry={() => void capacity.refetch()}
              />
            ) : null}
            <dl className="dataset-metrics">
              <div className="dataset-metric">
                <dt>状态</dt>
                <dd>{capacity.data.state}</dd>
              </div>
              <div className="dataset-metric">
                <dt>源字节</dt>
                <dd>{capacity.data.sourceBytes ?? '未知'}</dd>
              </div>
              <div className="dataset-metric">
                <dt>必需物理字节</dt>
                <dd>{capacity.data.requiredPhysicalBytes ?? '未知'}</dd>
              </div>
              <div className="dataset-metric">
                <dt>实际 OSS 字节</dt>
                <dd>{capacity.data.actualOssBytes ?? '未知'}</dd>
              </div>
            </dl>
            <p>
              Basis <code>{capacity.data.basisRevision}</code> ·{' '}
              {new Date(capacity.data.calculatedAt).toLocaleString()}
            </p>
          </section>
        )
      ) : null}
    </main>
  );
}

export default DatasetDetailPage;
