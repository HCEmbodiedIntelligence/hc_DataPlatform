import type { ReactNode } from 'react';

export type DatasetRegionState =
  | 'ready'
  | 'first-loading'
  | 'refreshing'
  | 'empty'
  | 'filtered-empty'
  | 'partial-error'
  | 'fatal-error'
  | 'forbidden'
  | 'not-found'
  | 'gone'
  | 'conflict'
  | 'rate-limited'
  | 'offline'
  | 'reconnecting'
  | 'contract-mismatch'
  | 'unknown-enum'
  | 'feature-unavailable';

export type RegionStateProps = Readonly<{
  state: DatasetRegionState;
  title?: string;
  message?: string;
  requestId?: string | null;
  retryAfterSeconds?: number;
  onRetry?: () => void;
  children?: ReactNode;
}>;

const DEFAULT_COPY: Readonly<
  Record<Exclude<DatasetRegionState, 'ready'>, readonly [string, string]>
> = {
  'first-loading': ['正在加载', '正在读取经过合同校验的数据。'],
  refreshing: ['正在刷新', '保留上次成功结果，危险操作暂时关闭。'],
  empty: ['暂无数据', '当前范围内还没有可显示的事实。'],
  'filtered-empty': ['没有匹配结果', '可调整或清除筛选条件。'],
  'partial-error': ['部分区域不可用', '其他独立区域仍可继续使用。'],
  'fatal-error': ['页面加载失败', '请重试或返回安全入口。'],
  forbidden: ['无权访问', '未请求或保留受保护的领域数据。'],
  'not-found': ['资源不存在', '稳定 ID 未找到，未回退到 current/latest。'],
  gone: ['资源已失效', '资源曾存在，但当前已过期或不可恢复。'],
  conflict: ['检测到并发冲突', '已保留输入；请比较最新事实后重新确认。'],
  'rate-limited': ['请求过于频繁', '将在服务端允许后才能重试。'],
  offline: ['当前离线', '显示的数据可能陈旧，写操作已经关闭。'],
  reconnecting: ['正在重新连接', '读取继续保留，写操作等待连接恢复。'],
  'contract-mismatch': ['数据合同不匹配', '受影响区域已安全阻断，未使用猜测值。'],
  'unknown-enum': ['发现未知状态', '当前区域只读，相关写操作已关闭。'],
  'feature-unavailable': ['功能尚不可用', '所需 capability 或后端命令尚未冻结。'],
};

export function RegionState({
  state,
  title,
  message,
  requestId,
  retryAfterSeconds,
  onRetry,
  children,
}: RegionStateProps) {
  if (state === 'ready') return <>{children}</>;

  const [defaultTitle, defaultMessage] = DEFAULT_COPY[state];
  if (state === 'first-loading') {
    return (
      <section
        className="dataset-region-state dataset-region-state--loading"
        aria-busy="true"
        aria-label={title ?? defaultTitle}
      >
        <span className="dataset-skeleton dataset-skeleton--title" />
        <span className="dataset-skeleton" />
        <span className="dataset-skeleton dataset-skeleton--short" />
      </section>
    );
  }

  return (
    <section
      className={`dataset-region-state dataset-region-state--${state}`}
      role={state.includes('error') || state === 'contract-mismatch' ? 'alert' : 'status'}
    >
      <strong>{title ?? defaultTitle}</strong>
      <p>{message ?? defaultMessage}</p>
      {retryAfterSeconds !== undefined ? <small>约 {retryAfterSeconds} 秒后可重试</small> : null}
      {requestId ? (
        <small>
          Request ID: <code>{requestId}</code>
        </small>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          className="dataset-button dataset-button--secondary"
          onClick={onRetry}
        >
          重试
        </button>
      ) : null}
    </section>
  );
}
