import { useDeferredValue, useMemo, useState } from 'react';
import { Select } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';
import { useDatasetsQuery } from '../../features/datasets/api/hooks';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  datasetContextKind,
  datasetSelectionLocation,
  selectedDatasetId,
} from './dataset-context';
import styles from './DatasetContextSelector.module.css';

export interface DatasetContextSelectorProps {
  className?: string;
}

export default function DatasetContextSelector({ className }: DatasetContextSelectorProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const capabilities = useCapabilities();
  const projectId = useShellStore((state) => state.scope?.projectId);
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query.trim());
  const context = datasetContextKind(location.pathname);
  const canRead =
    context !== null &&
    Boolean(projectId) &&
    !capabilities.loading &&
    !capabilities.failed &&
    capabilities.has('dataset.read');
  const filters = useMemo(
    () => ({
      ...(deferredQuery ? { q: deferredQuery } : {}),
      sort: 'nameAsc' as const,
      limit: 100 as const,
    }),
    [deferredQuery],
  );
  const datasets = useDatasetsQuery(filters, canRead);
  const currentDatasetId = selectedDatasetId(location.pathname, location.search);
  const options = useMemo(() => {
    const values: Array<{ label: string; value: string }> =
      datasets.data?.items.map((dataset) => ({
        label: dataset.name,
        value: dataset.datasetId,
      })) ?? [];
    if (currentDatasetId && !values.some((option) => option.value === currentDatasetId)) {
      values.unshift({ label: currentDatasetId, value: currentDatasetId });
    }
    return values;
  }, [currentDatasetId, datasets.data?.items]);

  if (context === null) return null;

  const selectDataset = (datasetId?: string) => {
    setQuery('');
    void navigate(datasetSelectionLocation(location.pathname, location.search, datasetId));
  };

  return (
    <Select
      allowClear
      aria-label="选择数据集"
      className={[styles.selector, className].filter(Boolean).join(' ')}
      disabled={!canRead}
      filterOption={false}
      loading={capabilities.loading || datasets.isFetching}
      notFoundContent={
        datasets.isError ? '数据集加载失败' : deferredQuery ? '没有匹配的数据集' : '暂无数据集'
      }
      optionRender={(option) => (
        <span className={styles.option}>
          <strong>{option.label}</strong>
          <small>{String(option.value)}</small>
        </span>
      )}
      options={options}
      placeholder="搜索并选择数据集"
      showSearch
      value={currentDatasetId}
      onChange={(value) => selectDataset(value)}
      onSearch={setQuery}
    />
  );
}
