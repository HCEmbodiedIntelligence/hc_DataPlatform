import { Button, Input, Select } from 'antd';
import { ChevronDown, ChevronUp, RotateCcw } from 'lucide-react';
import { useEffect, useId, useState, type FormEvent } from 'react';
import type { DatasetFacetsVm } from '../../../features/datasets/api';
import type { DatasetsSearch } from '../query-codec';
import styles from '../styles.module.css';

type FilterDraft = Readonly<{
  q: string;
  robotModelId: string;
  robotId: string;
  task: string;
  scene: string;
  assetState: string;
  workflowState: string;
  storageClass: string;
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
    workflowState: search.workflowState ?? '',
    storageClass: search.storageClass ?? '',
    datasetCreatedFrom: search.datasetCreatedFrom ?? '',
    datasetCreatedTo: search.datasetCreatedTo ?? '',
    sort: search.sort,
    limit: search.limit,
  };
}

function clearedDraft(search: DatasetsSearch): FilterDraft {
  return {
    ...draftFromSearch(search),
    q: '',
    robotModelId: '',
    robotId: '',
    task: '',
    scene: '',
    assetState: '',
    workflowState: '',
    storageClass: '',
    datasetCreatedFrom: '',
    datasetCreatedTo: '',
  };
}

function facetOptions(items: readonly { readonly value: string; readonly count: string }[] | undefined) {
  return [
    { label: '全部', value: '' },
    ...(items ?? []).map((item) => ({
      label: `${item.value} (${item.count})`,
      value: item.value,
    })),
  ];
}

export function activeDatasetFilterCount(search: DatasetsSearch): number {
  return [
    search.q,
    search.robotModelId,
    search.robotId,
    search.task,
    search.scene,
    search.assetState,
    search.workflowState,
    search.storageClass,
    search.datasetCreatedFrom || search.datasetCreatedTo,
  ].filter(Boolean).length;
}

export function DatasetFilterPanel({
  search,
  facets,
  disabled = false,
  busy = false,
  onApply,
  onReset,
}: Readonly<{
  search: DatasetsSearch;
  facets?: DatasetFacetsVm;
  disabled?: boolean;
  busy?: boolean;
  onApply: (changes: Partial<DatasetsSearch>) => void;
  onReset: () => void;
}>) {
  const [draft, setDraft] = useState(() => draftFromSearch(search));
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();
  const activeCount = activeDatasetFilterCount(search);
  const controlsDisabled = disabled || busy;

  useEffect(() => setDraft(draftFromSearch(search)), [search]);

  const apply = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (controlsDisabled) return;
    onApply({
      q: draft.q.trim() || undefined,
      robotModelId: draft.robotModelId || undefined,
      robotId: draft.robotId || undefined,
      task: draft.task.trim() || undefined,
      scene: draft.scene.trim() || undefined,
      assetState: (draft.assetState || undefined) as DatasetsSearch['assetState'],
      workflowState: (draft.workflowState || undefined) as DatasetsSearch['workflowState'],
      storageClass: (draft.storageClass || undefined) as DatasetsSearch['storageClass'],
      datasetCreatedFrom: draft.datasetCreatedFrom || undefined,
      datasetCreatedTo: draft.datasetCreatedTo || undefined,
      sort: draft.sort,
      limit: draft.limit,
    });
    setExpanded(false);
  };

  return (
    <section className={styles.filterPanel} aria-labelledby={`${panelId}-title`}>
      <div className={styles.filterPanelHeader}>
        <h2 id={`${panelId}-title`}>{activeCount > 0 ? `筛选条件（${activeCount}）` : '筛选条件'}</h2>
        <Button
          type="text"
          className={styles.filterToggle}
          aria-expanded={expanded}
          aria-controls={panelId}
          icon={expanded ? <ChevronUp aria-hidden="true" size={16} /> : <ChevronDown aria-hidden="true" size={16} />}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? '收起' : '展开'}
        </Button>
      </div>

      {expanded ? (
        <form id={panelId} className={styles.filterForm} aria-label="筛选条件" onSubmit={apply}>
          <div className={styles.filterFields}>
            <label className={styles.filterField}>
              <span>数据集名称搜索</span>
              <Input
                value={draft.q}
                placeholder="输入数据集名称"
                allowClear
                disabled={controlsDisabled}
                onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>机器人型号</span>
              <Select
                value={draft.robotModelId}
                options={facetOptions(facets?.robotModels)}
                disabled={controlsDisabled}
                onChange={(robotModelId) => setDraft((current) => ({ ...current, robotModelId }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>机器人</span>
              <Select
                value={draft.robotId}
                options={facetOptions(facets?.robots)}
                disabled={controlsDisabled}
                onChange={(robotId) => setDraft((current) => ({ ...current, robotId }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>检索任务</span>
              <Input
                value={draft.task}
                placeholder="输入任务名称或 ID"
                allowClear
                disabled={controlsDisabled}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    task: event.target.value,
                  }))
                }
              />
            </label>
            <label className={styles.filterField}>
              <span>场景</span>
              <Select
                value={draft.scene}
                options={facetOptions(facets?.scenes)}
                disabled={controlsDisabled}
                onChange={(scene) => setDraft((current) => ({ ...current, scene }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>资产状态</span>
              <Select
                value={draft.assetState}
                options={facetOptions(facets?.assetStates)}
                disabled={controlsDisabled}
                onChange={(assetState) => setDraft((current) => ({ ...current, assetState }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>处理状态</span>
              <Select
                value={draft.workflowState}
                disabled={controlsDisabled}
                options={[
                  { label: '全部', value: '' },
                  { label: '待复核', value: 'pendingReview' },
                  { label: '需返工', value: 'returned' },
                  { label: '有可处理草稿', value: 'actionableDraft' },
                ]}
                onChange={(workflowState) => setDraft((current) => ({ ...current, workflowState }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>存储层级</span>
              <Select
                value={draft.storageClass}
                options={facetOptions(facets?.storageClasses)}
                disabled={controlsDisabled}
                onChange={(storageClass) => setDraft((current) => ({ ...current, storageClass }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>创建起始日期</span>
              <Input
                type="date"
                value={draft.datasetCreatedFrom}
                disabled={controlsDisabled}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    datasetCreatedFrom: event.target.value,
                  }))
                }
              />
            </label>
            <label className={styles.filterField}>
              <span>创建结束日期</span>
              <Input
                type="date"
                value={draft.datasetCreatedTo}
                disabled={controlsDisabled}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    datasetCreatedTo: event.target.value,
                  }))
                }
              />
            </label>
            <label className={styles.filterField}>
              <span>稳定排序</span>
              <Select
                value={draft.sort}
                disabled={controlsDisabled}
                options={[
                  { label: '最近活动（ID 降序兜底）', value: 'activityDesc' },
                  { label: '最近创建（ID 降序兜底）', value: 'createdDesc' },
                  { label: '名称（ID 升序兜底）', value: 'nameAsc' },
                ]}
                onChange={(sort) => setDraft((current) => ({ ...current, sort }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>每页数量</span>
              <Select
                value={draft.limit}
                disabled={controlsDisabled}
                options={[20, 50, 100].map((limit) => ({
                  label: String(limit),
                  value: limit,
                }))}
                onChange={(limit) => setDraft((current) => ({ ...current, limit }))}
              />
            </label>
          </div>
          <div className={styles.filterActions}>
            <Button
              type="default"
              icon={<RotateCcw aria-hidden="true" size={16} />}
              disabled={controlsDisabled}
              onClick={() => {
                setDraft(clearedDraft(search));
                onReset();
              }}
            >
              重置
            </Button>
            <Button type="primary" htmlType="submit" loading={busy} disabled={disabled}>
              应用筛选
            </Button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
