import { Button } from 'antd';
import {
  Archive,
  ArrowRight,
  Clock3,
  Database,
  FileStack,
  GitBranch,
  ListChecks,
  NotebookTabs,
} from 'lucide-react';
import type { ReactNode } from 'react';
import type { DatasetId } from '../../../entities/dataset';
import type { DatasetListItemVm } from '../../../features/datasets/api';
import { StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

function actionAllowed(item: DatasetListItemVm, action: string): boolean {
  return item.allowedActions.some((candidate) => candidate.action === action && candidate.allowed);
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function CountFact({
  label,
  value,
  detail,
  icon,
  tone = 'default',
}: Readonly<{
  label: string;
  value: string;
  detail: string;
  icon: ReactNode;
  tone?: 'default' | 'warning' | 'success';
}>) {
  return (
    <article className={styles.selectedFact} data-tone={tone}>
      <span className={styles.selectedFactIcon} aria-hidden="true">
        {icon}
      </span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </article>
  );
}

export function SelectedDatasetSummary({
  item,
  position,
  total,
  canReadEpisodes,
  onOpen,
  onOpenEpisodes,
}: Readonly<{
  item: DatasetListItemVm;
  position: number;
  total: number;
  canReadEpisodes: boolean;
  onOpen: (datasetId: DatasetId) => void;
  onOpenEpisodes: (item: DatasetListItemVm) => void;
}>) {
  const current = item.currentVersion;
  const canOpen = actionAllowed(item, 'OPEN_DATASET');
  const canOpenEpisodes =
    current !== null && canReadEpisodes && actionAllowed(item, 'OPEN_EPISODE');

  return (
    <section className={styles.selectedSummary} aria-label="当前选中数据集">
      <header className={styles.selectedHeader}>
        <span className={styles.selectedDatasetIcon} aria-hidden="true">
          <Database size={22} strokeWidth={1.8} />
        </span>
        <div className={styles.selectedIdentity}>
          <span className={styles.selectedEyebrow}>
            当前选中 · {position} / {total}
          </span>
          <h2>{item.name}</h2>
          <code>ID · {item.datasetId}</code>
        </div>
        <StatusTag
          status={current?.kind ?? 'PENDING_INGEST'}
          label={current ? `当前 Ready · ${current.displayVersion}` : '待导入'}
          tone={current ? 'success' : 'warning'}
          known={current?.kind !== 'UNKNOWN'}
        />
        <div className={styles.selectedActions}>
          <Button disabled={!canOpen} onClick={() => onOpen(item.datasetId)}>
            打开数据集
          </Button>
          <Button
            type="primary"
            icon={<ArrowRight aria-hidden="true" size={15} />}
            disabled={!canOpenEpisodes}
            onClick={() => onOpenEpisodes(item)}
          >
            查看 Episodes
          </Button>
        </div>
      </header>

      <div className={styles.selectedFacts}>
        <article className={`${styles.selectedFact} ${styles.versionFact}`}>
          <span className={styles.selectedFactIcon} aria-hidden="true">
            <GitBranch size={20} />
          </span>
          <div>
            <span>当前 Ready 版本</span>
            <strong>{current ? `${current.displayVersion} · ${current.kind}` : '尚无版本'}</strong>
            <code>{current?.versionId ?? '等待首次导入'}</code>
          </div>
        </article>
        <CountFact
          label="Episodes"
          value={item.episodeCount}
          detail="当前可读"
          icon={<FileStack size={20} />}
          tone="success"
        />
        <CountFact
          label="待复核"
          value={item.pendingReviewVersionCount}
          detail="等待确认"
          icon={<ListChecks size={20} />}
          tone="warning"
        />
        <CountFact
          label="已退回"
          value={item.returnedVersionCount}
          detail="需要处理"
          icon={<Archive size={20} />}
          tone="warning"
        />
        <CountFact
          label="可处理草稿"
          value={item.actionableDraftCount}
          detail="已授权下一步"
          icon={<NotebookTabs size={20} />}
          tone="success"
        />
      </div>

      <footer className={styles.selectedTimeline}>
        <Clock3 aria-hidden="true" size={15} />
        <span>
          创建 <time dateTime={item.datasetCreatedAt}>{formatDateTime(item.datasetCreatedAt)}</time>
        </span>
        <span>
          最近活动{' '}
          <time dateTime={item.datasetActivityAt}>{formatDateTime(item.datasetActivityAt)}</time>
        </span>
        {current ? (
          <span>
            Ready 发布{' '}
            <time dateTime={current.publishedAt}>{formatDateTime(current.publishedAt)}</time>
          </span>
        ) : null}
      </footer>
    </section>
  );
}
