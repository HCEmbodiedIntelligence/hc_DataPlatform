import { Input, Select } from 'antd';
import { useEffect, useState } from 'react';
import type { DatasetFacetsVm } from '../../../features/datasets/api';
import { FilterToolbar } from '../../../shared/ui';
import type { DatasetsSearch } from '../query-codec';
import styles from '../styles.module.css';

type FilterDraft = Readonly<{
  q: string;
  robotModelId: string;
  robotId: string;
  task: string;
  scene: string;
  assetState: string;
  storageClass: string;
  channels: string;
  channelMatch: DatasetsSearch['channelMatch'];
  datasetCreatedFrom: string;
  datasetCreatedTo: string;
  sort: DatasetsSearch['sort'];
  limit: DatasetsSearch['limit'];
}>;

function draftFromSearch(search: DatasetsSearch): FilterDraft {
  return {
    q: search.q ?? '',
    robotModelId: search.robotModelId ?? '',
    robotId: search.robotId ?? '',
    task: search.task ?? '',
    scene: search.scene ?? '',
    assetState: search.assetState ?? '',
    storageClass: search.storageClass ?? '',
    channels: search.channels?.join(', ') ?? '',
    channelMatch: search.channelMatch,
    datasetCreatedFrom: search.datasetCreatedFrom ?? '',
    datasetCreatedTo: search.datasetCreatedTo ?? '',
    sort: search.sort,
    limit: search.limit,
  };
}

function facetOptions(items: readonly { readonly value: string; readonly count: string }[] | undefined) {
  return [
    { label: '全部', value: '' },
    ...(items ?? []).map((item) => ({ label: `${item.value} (${item.count})`, value: item.value })),
  ];
}

export function DatasetFilterPanel({
  search,
  facets,
  disabled = false,
  onApply,
  onReset,
}: Readonly<{
  search: DatasetsSearch;
  facets?: DatasetFacetsVm;
  disabled?: boolean;
  onApply: (changes: Partial<DatasetsSearch>) => void;
  onReset: () => void;
}>) {
  const [draft, setDraft] = useState(() => draftFromSearch(search));
  useEffect(() => setDraft(draftFromSearch(search)), [search]);

  const apply = () => {
    onApply({
      q: draft.q.trim() || undefined,
      robotModelId: draft.robotModelId || undefined,
      robotId: draft.robotId || undefined,
      task: draft.task.trim() || undefined,
      scene: draft.scene.trim() || undefined,
      assetState: (draft.assetState || undefined) as DatasetsSearch['assetState'],
      storageClass: (draft.storageClass || undefined) as DatasetsSearch['storageClass'],
      channels: [
        ...new Set(
          draft.channels
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
        ),
      ].sort(),
      channelMatch: draft.channelMatch,
      datasetCreatedFrom: draft.datasetCreatedFrom || undefined,
      datasetCreatedTo: draft.datasetCreatedTo || undefined,
      sort: draft.sort,
      limit: draft.limit,
    });
  };

  return (
    <FilterToolbar
      label="数据集筛选"
      applyLabel="应用筛选"
      disabled={disabled}
      onApply={apply}
      onReset={() => {
        setDraft(draftFromSearch(datasetsDefaults));
        onReset();
      }}
    >
      <label className={styles.filterField}>
        <span>搜索</span>
        <Input
          value={draft.q}
          placeholder="数据集名称"
          allowClear
          onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>机器人型号</span>
        <Select
          value={draft.robotModelId}
          options={facetOptions(facets?.robotModels)}
          onChange={(robotModelId) => setDraft((current) => ({ ...current, robotModelId }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>机器人</span>
        <Select
          value={draft.robotId}
          options={facetOptions(facets?.robots)}
          onChange={(robotId) => setDraft((current) => ({ ...current, robotId }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>任务</span>
        <Select
          value={draft.task}
          options={facetOptions(facets?.tasks)}
          onChange={(task) => setDraft((current) => ({ ...current, task }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>场景</span>
        <Select
          value={draft.scene}
          options={facetOptions(facets?.scenes)}
          onChange={(scene) => setDraft((current) => ({ ...current, scene }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>资产状态</span>
        <Select
          value={draft.assetState}
          options={facetOptions(facets?.assetStates)}
          onChange={(assetState) => setDraft((current) => ({ ...current, assetState }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>存储层级</span>
        <Select
          value={draft.storageClass}
          options={facetOptions(facets?.storageClasses)}
          onChange={(storageClass) => setDraft((current) => ({ ...current, storageClass }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>Channels（逗号分隔）</span>
        <Input
          value={draft.channels}
          placeholder="/camera/front, /joint"
          onChange={(event) => setDraft((current) => ({ ...current, channels: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>Channel 匹配</span>
        <Select
          value={draft.channelMatch}
          options={[
            { label: '全部包含', value: 'all' },
            { label: '任一包含', value: 'any' },
          ]}
          onChange={(channelMatch) => setDraft((current) => ({ ...current, channelMatch }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>创建起始日</span>
        <Input
          type="date"
          value={draft.datasetCreatedFrom}
          onChange={(event) => setDraft((current) => ({ ...current, datasetCreatedFrom: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>创建结束日</span>
        <Input
          type="date"
          value={draft.datasetCreatedTo}
          onChange={(event) => setDraft((current) => ({ ...current, datasetCreatedTo: event.target.value }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>稳定排序</span>
        <Select
          value={draft.sort}
          options={[
            { label: '最近活动（ID 降序兜底）', value: 'activityDesc' },
            { label: '最近创建（ID 降序兜底）', value: 'createdDesc' },
            { label: '名称（ID 升序兜底）', value: 'nameAsc' },
          ]}
          onChange={(sort) => setDraft((current) => ({ ...current, sort }))}
        />
      </label>
      <label className={styles.filterField}>
        <span>每页</span>
        <Select
          value={draft.limit}
          options={[20, 50, 100].map((limit) => ({ label: String(limit), value: limit }))}
          onChange={(limit) => setDraft((current) => ({ ...current, limit }))}
        />
      </label>
    </FilterToolbar>
  );
}

const datasetsDefaults: DatasetsSearch = {
  channelMatch: 'all',
  sort: 'activityDesc',
  limit: 20,
};
