import {
  asSearchParams,
  canonicalCursorPair,
  cleanCursor,
  cleanId,
  cleanText,
  defineQueryCodec,
  enumValue,
  valuesChanged,
} from '../../features/datasets/query-codec';
import type {
  DatasetAssetState,
  DatasetStorageClass,
  DatasetWorkflowState,
  DatasetsRouteFilters,
  DatasetsSort,
} from '../../features/datasets/routing';

const SORTS = ['activityDesc', 'createdDesc', 'nameAsc'] as const;
const ASSET_STATES = ['ready', 'validating', 'problem', 'frozen', 'pending_ingest', 'unknown'] as const;
const STORAGE_CLASSES = ['standard', 'ia', 'archive'] as const;
const WORKFLOW_STATES = ['pendingReview', 'returned', 'actionableDraft'] as const;
const LIMITS = ['20', '50', '100'] as const;
const COLLECTION_TASK_ID = /^[A-Za-z0-9._-]{1,128}$/;

export type DatasetsSearch = Omit<DatasetsRouteFilters, 'sort' | 'limit'> &
  Readonly<{
    sort: DatasetsSort;
    limit: 20 | 50 | 100;
  }>;

function parseDatasetsSearch(input: string | URLSearchParams): DatasetsSearch {
  const params = asSearchParams(input);
  const cursor = canonicalCursorPair(params);
  const limit = enumValue(params.get('limit'), LIMITS);
  return {
    collectionTaskId: cleanId(params.get('collectionTaskId'), COLLECTION_TASK_ID),
    q: cleanText(params.get('q'), 200),
    robotModelId: cleanText(params.get('robotModelId'), 128),
    robotId: cleanText(params.get('robotId'), 128),
    task: cleanText(params.get('task'), 128),
    scene: cleanText(params.get('scene'), 128),
    assetState: enumValue(params.get('assetState'), ASSET_STATES) as DatasetAssetState | undefined,
    workflowState: enumValue(params.get('workflowState'), WORKFLOW_STATES) as DatasetWorkflowState | undefined,
    storageClass: enumValue(params.get('storageClass'), STORAGE_CLASSES) as DatasetStorageClass | undefined,
    datasetCreatedFrom: cleanText(params.get('datasetCreatedFrom'), 40),
    datasetCreatedTo: cleanText(params.get('datasetCreatedTo'), 40),
    sort: enumValue(params.get('sort'), SORTS) ?? 'activityDesc',
    ...cursor,
    limit: limit ? (Number(limit) as 20 | 50 | 100) : 20,
  };
}

function buildDatasetsSearch(input: DatasetsRouteFilters): URLSearchParams {
  const raw = new URLSearchParams(
    Object.entries({
      collectionTaskId: input.collectionTaskId,
      q: input.q,
      robotModelId: input.robotModelId,
      robotId: input.robotId,
      task: input.task,
      scene: input.scene,
      assetState: input.assetState,
      workflowState: input.workflowState,
      storageClass: input.storageClass,
      datasetCreatedFrom: input.datasetCreatedFrom,
      datasetCreatedTo: input.datasetCreatedTo,
      sort: input.sort,
      after: cleanCursor(input.after),
      before: cleanCursor(input.before),
      limit: input.limit ? String(input.limit) : undefined,
    }).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
  );
  const normalized = parseDatasetsSearch(raw);
  const params = new URLSearchParams();
  for (const key of [
    'collectionTaskId',
    'q',
    'robotModelId',
    'robotId',
    'task',
    'scene',
    'assetState',
    'workflowState',
    'storageClass',
    'datasetCreatedFrom',
    'datasetCreatedTo',
  ] as const) {
    const value = normalized[key];
    if (typeof value === 'string' && value) params.set(key, value);
  }
  if (normalized.sort !== 'activityDesc') params.set('sort', normalized.sort);
  if (normalized.after) params.set('after', normalized.after);
  if (normalized.before) params.set('before', normalized.before);
  if (normalized.limit !== 20) params.set('limit', String(normalized.limit));
  return params;
}

const PAGINATION_DIMENSIONS: readonly (keyof DatasetsSearch)[] = [
  'collectionTaskId',
  'q',
  'robotModelId',
  'robotId',
  'task',
  'scene',
  'assetState',
  'workflowState',
  'storageClass',
  'datasetCreatedFrom',
  'datasetCreatedTo',
  'sort',
  'limit',
];

export const datasetsQueryCodec = defineQueryCodec<DatasetsSearch, DatasetsRouteFilters>({
  parse: parseDatasetsSearch,
  build: buildDatasetsSearch,
  canonicalize(input) {
    return buildDatasetsSearch(parseDatasetsSearch(input)).toString();
  },
  withChanges(current, changes, scopeChanged = false) {
    const clearCursor = scopeChanged || valuesChanged(current, changes, PAGINATION_DIMENSIONS);
    return parseDatasetsSearch(
      buildDatasetsSearch({
        ...current,
        ...changes,
        ...(clearCursor ? { after: undefined, before: undefined } : {}),
      }),
    );
  },
});

export default datasetsQueryCodec;
