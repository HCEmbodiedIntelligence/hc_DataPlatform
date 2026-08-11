import { useEffect, useState, type FormEvent } from 'react';
import type { DatasetFacetsVm } from '../../../features/datasets/api';
import type { DatasetsSearch } from '../query-codec';

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

export function DatasetFilterPanel({
  search,
  facets,
  onApply,
  onReset,
}: Readonly<{
  search: DatasetsSearch;
  facets?: DatasetFacetsVm;
  onApply: (changes: Partial<DatasetsSearch>) => void;
  onReset: () => void;
}>) {
  const [draft, setDraft] = useState(() => draftFromSearch(search));
  useEffect(() => setDraft(draftFromSearch(search)), [search]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
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
    <form className="dataset-filter-bar" aria-label="数据集筛选" onSubmit={submit}>
      <label>
        搜索
        <input
          value={draft.q}
          placeholder="数据集名称"
          onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))}
        />
      </label>
      <label>
        机器人型号
        <select
          value={draft.robotModelId}
          onChange={(event) =>
            setDraft((current) => ({ ...current, robotModelId: event.target.value }))
          }
        >
          <option value="">全部</option>
          {facets?.robotModels.map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
      </label>
      <label>
        机器人
        <select
          value={draft.robotId}
          onChange={(event) => setDraft((current) => ({ ...current, robotId: event.target.value }))}
        >
          <option value="">全部</option>
          {facets?.robots.map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
      </label>
      <label>
        任务
        <select
          value={draft.task}
          onChange={(event) => setDraft((current) => ({ ...current, task: event.target.value }))}
        >
          <option value="">全部</option>
          {facets?.tasks.map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
      </label>
      <label>
        场景
        <select
          value={draft.scene}
          onChange={(event) => setDraft((current) => ({ ...current, scene: event.target.value }))}
        >
          <option value="">全部</option>
          {facets?.scenes.map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
      </label>
      <label>
        资产状态
        <select
          value={draft.assetState}
          onChange={(event) =>
            setDraft((current) => ({ ...current, assetState: event.target.value }))
          }
        >
          <option value="">全部</option>
          {facets?.assetStates.map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
      </label>
      <label>
        存储层级
        <select
          value={draft.storageClass}
          onChange={(event) =>
            setDraft((current) => ({ ...current, storageClass: event.target.value }))
          }
        >
          <option value="">全部</option>
          {facets?.storageClasses.map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
      </label>
      <label>
        Channels（逗号分隔）
        <input
          value={draft.channels}
          placeholder="/camera/front, /joint"
          onChange={(event) =>
            setDraft((current) => ({ ...current, channels: event.target.value }))
          }
        />
      </label>
      <label>
        Channel 匹配
        <select
          value={draft.channelMatch}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              channelMatch: event.target.value as DatasetsSearch['channelMatch'],
            }))
          }
        >
          <option value="all">全部包含</option>
          <option value="any">任一包含</option>
        </select>
      </label>
      <label>
        创建起始日
        <input
          type="date"
          value={draft.datasetCreatedFrom}
          onChange={(event) =>
            setDraft((current) => ({ ...current, datasetCreatedFrom: event.target.value }))
          }
        />
      </label>
      <label>
        创建结束日
        <input
          type="date"
          value={draft.datasetCreatedTo}
          onChange={(event) =>
            setDraft((current) => ({ ...current, datasetCreatedTo: event.target.value }))
          }
        />
      </label>
      <label>
        稳定排序
        <select
          value={draft.sort}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              sort: event.target.value as DatasetsSearch['sort'],
            }))
          }
        >
          <option value="activityDesc">最近活动（ID 降序兜底）</option>
          <option value="createdDesc">最近创建（ID 降序兜底）</option>
          <option value="nameAsc">名称（ID 升序兜底）</option>
        </select>
      </label>
      <label>
        每页
        <select
          value={draft.limit}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              limit: Number(event.target.value) as DatasetsSearch['limit'],
            }))
          }
        >
          <option value="20">20</option>
          <option value="50">50</option>
          <option value="100">100</option>
        </select>
      </label>
      <div className="dataset-filter-actions">
        <button type="submit" className="dataset-button">
          应用筛选
        </button>
        <button
          type="button"
          className="dataset-button dataset-button--secondary"
          onClick={onReset}
        >
          清除
        </button>
      </div>
    </form>
  );
}
