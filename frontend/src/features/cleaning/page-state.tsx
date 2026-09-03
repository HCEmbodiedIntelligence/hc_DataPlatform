/* eslint-disable react-refresh/only-export-components -- state mapping and renderer form one page-state boundary. */
import type { ReactNode } from 'react';
import { isDomainError, type DomainError } from '../../shared/api/domain-error';
import { EmptyState, ErrorPanel, ForbiddenPanel, SkeletonBlock } from '../../shared/ui';

export const CLEANING_PAGE_STATES = [
  'first-loading', 'refreshing', 'empty', 'filtered-empty', 'partial-error', 'fatal-error',
  'forbidden', 'not-found/gone', 'conflict', 'rate-limited', 'offline/reconnecting',
  'contract-mismatch', 'unknown-enum', 'feature-unavailable',
] as const;
export type CleaningPageState = (typeof CLEANING_PAGE_STATES)[number];

export function cleaningStateFromError(error: unknown, fatal = false): CleaningPageState {
  if (!isDomainError(error)) return 'contract-mismatch';
  switch (error.code) {
    case 'FORBIDDEN': return 'forbidden';
    case 'NOT_FOUND':
    case 'GONE': return 'not-found/gone';
    case 'VERSION_CONFLICT':
    case 'PRECONDITION_FAILED': return 'conflict';
    case 'RATE_LIMITED': return 'rate-limited';
    case 'NETWORK_ERROR': return 'offline/reconnecting';
    case 'CONTRACT_MISMATCH': return 'contract-mismatch';
    default: return fatal ? 'fatal-error' : 'partial-error';
  }
}

const STATE_COPY: Readonly<Record<Exclude<CleaningPageState,
  'first-loading' | 'refreshing' | 'empty' | 'filtered-empty' | 'forbidden' | 'partial-error' | 'fatal-error'
>, string>> = {
  'not-found/gone': '资源不存在、已归档或不再属于当前作用域。',
  conflict: '服务器事实已变化。已保留本地输入，请刷新并重新确认。',
  'rate-limited': '请求频率受限。请等待服务端允许的重试时间后手动重试。',
  'offline/reconnecting': '网络中断，当前数据可能陈旧；恢复连接前写操作已禁用。',
  'contract-mismatch': '响应未通过合同校验。为避免写错资源，此区域已安全关闭。',
  'unknown-enum': '服务端返回了当前客户端未知的状态；该资源仅可读取。',
  'feature-unavailable': '此能力的服务端合同尚未提供，当前不展示半成品写入口。',
};

export function CleaningStatePanel({
  state,
  label,
  error,
  filtered = false,
  onRetry,
  children,
}: Readonly<{
  state: CleaningPageState;
  label: string;
  error?: unknown;
  filtered?: boolean;
  onRetry?: () => void;
  children?: ReactNode;
}>) {
  if (state === 'first-loading') {
    return <SkeletonBlock width="100%" height="24rem" label={`${label}加载中`} />;
  }
  if (state === 'forbidden') return <ForbiddenPanel />;
  if (state === 'empty' || state === 'filtered-empty') {
    return <EmptyState kind={filtered || state === 'filtered-empty' ? 'filtered-empty' : 'no-data'} />;
  }
  if ((state === 'partial-error' || state === 'fatal-error') && isDomainError(error)) {
    return <ErrorPanel error={error as DomainError} onRetry={onRetry} />;
  }
  if (state !== 'refreshing' && state !== 'partial-error' && state !== 'fatal-error') {
    return (
      <section className="cleaning-state-panel" role="status" data-state={state}>
        <h2>{label}</h2><p>{STATE_COPY[state]}</p>
        {isDomainError(error) && error.requestId ? <p>请求 ID：<code>{error.requestId}</code></p> : null}
        {onRetry ? <button type="button" onClick={onRetry}>重试</button> : null}
      </section>
    );
  }
  return (
    <section aria-busy={state === 'refreshing'}>
      {state === 'refreshing' ? <p className="cleaning-refreshing" role="status">正在刷新，危险操作暂不可用。</p> : null}
      {children}
    </section>
  );
}
