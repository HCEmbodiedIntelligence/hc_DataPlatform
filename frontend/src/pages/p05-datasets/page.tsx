import { useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { DatasetId } from '../../entities/dataset';
import {
  useCreateDatasetMutation,
  useDatasetFacetsQuery,
  useDatasetsPageCapabilitiesQuery,
  useDatasetSummaryQuery,
  useDatasetsQuery,
} from '../../features/datasets/api';
import { RegionState } from '../../features/datasets/components/RegionState';
import { datasetRegionStateForError } from '../../features/datasets/components/error-state';
import { routes } from '../../features/datasets/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { CreateDatasetDialog } from './components/CreateDatasetDialog';
import { DatasetFilterPanel } from './components/DatasetFilterPanel';
import { DatasetSummaryStrip } from './components/DatasetSummaryStrip';
import { DatasetTable } from './components/DatasetTable';
import datasetsQueryCodec, { type DatasetsSearch } from './query-codec';
import '../../features/datasets/components/datasets.css';

function nextIdempotencyKey(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `dataset-create-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function hasFilters(search: DatasetsSearch): boolean {
  return Boolean(
    search.q ||
      search.robotModelId ||
      search.robotId ||
      search.task ||
      search.scene ||
      search.assetState ||
      search.storageClass ||
      search.channels?.length ||
      search.datasetCreatedFrom ||
      search.datasetCreatedTo,
  );
}

export function DatasetsPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const search = datasetsQueryCodec.parse(params);
  const capabilities = useCapabilities();
  const canRead = capabilities.has('dataset.read');
  const query = useDatasetsQuery(search, canRead);
  const summary = useDatasetSummaryQuery(search, canRead);
  const facets = useDatasetFacetsQuery(search, canRead);
  const pageCapabilities = useDatasetsPageCapabilitiesQuery(canRead);
  const createMutation = useCreateDatasetMutation();
  const [createOpen, setCreateOpen] = useState(false);
  const [createIntentKey, setCreateIntentKey] = useState<string | null>(null);
  const [createdDatasetId, setCreatedDatasetId] = useState<DatasetId | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [labels, setLabels] = useState('');

  const change = (changes: Partial<DatasetsSearch>) => {
    const next = datasetsQueryCodec.withChanges(search, changes);
    void navigate(routes.datasets.build(next), { replace: true });
  };

  const openCreate = () => {
    createMutation.reset();
    setCreateIntentKey(nextIdempotencyKey());
    setCreateOpen(true);
  };

  const closeCreate = () => {
    if (createMutation.isPending) return;
    setCreateOpen(false);
    setCreateIntentKey(null);
  };

  const submitCreate = (event: FormEvent) => {
    event.preventDefault();
    if (!createIntentKey) return;
    createMutation.mutate(
      {
        idempotencyKey: createIntentKey,
        command: {
          name,
          description,
          labels: [
            ...new Set(
              labels
                .split(',')
                .map((value) => value.trim())
                .filter(Boolean),
            ),
          ],
        },
      },
      {
        onSuccess: (created) => {
          setCreateOpen(false);
          setCreateIntentKey(null);
          setCreatedDatasetId(created.dataset_id as DatasetId);
          void navigate(routes.datasets.build({ ...search, after: undefined, before: undefined }), {
            replace: true,
          });
        },
      },
    );
  };

  const canCreate =
    capabilities.has('dataset.create') &&
    pageCapabilities.data?.allowedActions.includes('CREATE_DATASET') === true;

  return (
    <main className="dataset-page" data-page-id="P05">
      <header className="dataset-page-header">
        <div>
          <p className="dataset-eyebrow">Data assets</p>
          <h1>数据集</h1>
          <p>服务端筛选、稳定排序与游标分页；筛选变化会回到首个游标窗口。</p>
        </div>
        <div className="dataset-actions">
          <button
            type="button"
            className="dataset-button"
            disabled={!canCreate}
            onClick={openCreate}
          >
            创建数据集
          </button>
        </div>
      </header>

      {pageCapabilities.isError ? (
        <RegionState
          state={datasetRegionStateForError(pageCapabilities.error)}
          title="页面操作不可用"
          message="列表仍保持只读；创建和批量动作已关闭。"
          onRetry={() => void pageCapabilities.refetch()}
        />
      ) : null}
      {facets.isError ? (
        <RegionState
          state={datasetRegionStateForError(facets.error)}
          title="筛选项加载失败"
          message="可以继续使用 URL 中已有筛选或重试此区域。"
          onRetry={() => void facets.refetch()}
        />
      ) : null}
      <DatasetFilterPanel
        search={search}
        facets={facets.data}
        onApply={change}
        onReset={() => {
          void navigate(routes.datasets.build({}), { replace: true });
        }}
      />

      {summary.isError ? (
        <RegionState
          state={datasetRegionStateForError(summary.error)}
          message="摘要区域加载失败；列表仍可独立使用。"
          onRetry={() => void summary.refetch()}
        />
      ) : (
        <DatasetSummaryStrip summary={summary.data} />
      )}

      {createdDatasetId ? (
        <section className="dataset-band" role="status">
          <div className="dataset-band-heading">
            <div>
              <h2>数据集已创建</h2>
              <p>
                <code>{createdDatasetId}</code> 是空 Dataset；没有隐式创建 Version。
              </p>
            </div>
            <button
              type="button"
              className="dataset-button"
              onClick={() => {
                void navigate(routes.datasetDetail.build({ datasetId: createdDatasetId }));
              }}
            >
              查看数据集
            </button>
          </div>
        </section>
      ) : null}

      {capabilities.loading ? (
        <RegionState state="first-loading" />
      ) : capabilities.failed || !canRead ? (
        <RegionState state="forbidden" />
      ) : query.isPending ? (
        <RegionState state="first-loading" />
      ) : query.isError ? (
        <RegionState
          state={datasetRegionStateForError(query.error)}
          message={query.error instanceof Error ? query.error.message : undefined}
          requestId={isDomainError(query.error) ? query.error.requestId : null}
          onRetry={() => void query.refetch()}
        />
      ) : query.data.items.length === 0 ? (
        <RegionState state={hasFilters(search) ? 'filtered-empty' : 'empty'} />
      ) : (
        <DatasetTable
          page={query.data}
          refreshing={query.isFetching}
          canReadEpisodes={capabilities.has('episode.read')}
          onOpen={(datasetId) => {
            void navigate(routes.datasetDetail.build({ datasetId }));
          }}
          onOpenEpisodes={(item) => {
            if (!item.currentVersion) return;
            void navigate(
              routes.datasetDetail.build({
                datasetId: item.datasetId,
                tab: 'episodes',
                versionId: item.currentVersion.versionId,
              }),
            );
          }}
          onPrevious={() =>
            change({ before: query.data.pageInfo.before ?? undefined, after: undefined })
          }
          onNext={() =>
            change({ after: query.data.pageInfo.after ?? undefined, before: undefined })
          }
        />
      )}

      <CreateDatasetDialog
        open={createOpen}
        name={name}
        description={description}
        labels={labels}
        pending={createMutation.isPending}
        error={
          createMutation.isError
            ? createMutation.error instanceof Error
              ? createMutation.error.message
              : '创建失败'
            : undefined
        }
        onNameChange={setName}
        onDescriptionChange={setDescription}
        onLabelsChange={setLabels}
        onSubmit={submitCreate}
        onCancel={closeCreate}
      />
    </main>
  );
}

export default DatasetsPage;
