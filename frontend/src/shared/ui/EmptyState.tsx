import { Inbox, SearchX } from 'lucide-react';
import { useId, type ReactNode } from 'react';

export interface EmptyStateProps {
  kind: 'no-data' | 'filtered-empty';
  title?: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ kind, title, description, action }: EmptyStateProps) {
  const titleId = useId();
  const filtered = kind === 'filtered-empty';
  const Icon = filtered ? SearchX : Inbox;
  return (
    <section className="empty-state" aria-labelledby={titleId}>
      <Icon aria-hidden="true" />
      <h2 id={titleId}>{title ?? (filtered ? '当前筛选没有结果' : '暂无数据')}</h2>
      <p>{description ?? (filtered ? '调整或清除筛选条件后重试。' : '此范围内还没有可显示的数据。')}</p>
      {action ? <div>{action}</div> : null}
    </section>
  );
}
