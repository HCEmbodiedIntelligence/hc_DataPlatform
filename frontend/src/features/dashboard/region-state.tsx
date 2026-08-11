import type { ReactNode } from 'react';

export type DashboardRegionStatus =
  | 'ready'
  | 'first-loading'
  | 'refreshing'
  | 'empty'
  | 'filtered-empty'
  | 'partial-error'
  | 'fatal-error'
  | 'forbidden'
  | 'not-found-gone'
  | 'conflict'
  | 'rate-limited'
  | 'offline-reconnecting'
  | 'contract-mismatch'
  | 'unknown-enum'
  | 'feature-unavailable';

const messages: Record<Exclude<DashboardRegionStatus, 'ready'>, string> = {
  'first-loading': '正在加载工作台数据',
  refreshing: '正在刷新，当前显示上次成功数据',
  empty: '当前范围内暂无数据',
  'filtered-empty': '当前筛选没有结果，可清除筛选后重试',
  'partial-error': '此区域暂时不可用，其他区域仍可继续查看',
  'fatal-error': '工作台暂时无法加载',
  forbidden: '你没有查看工作台聚合数据的权限',
  'not-found-gone': '当前项目或区域不存在，或已失效',
  conflict: '列表快照已更新，请从第一组重新加载',
  'rate-limited': '请求过于频繁，请按服务端提示稍后重试',
  'offline-reconnecting': '网络已断开，正在等待恢复；当前数据可能已过期',
  'contract-mismatch': '服务端数据与页面合同不一致，此区域已安全阻断',
  'unknown-enum': '发现新状态，已按只读模式安全展示',
  'feature-unavailable': '此功能尚未在当前环境开放',
};

export function DashboardRegionState(props: Readonly<{
  status: DashboardRegionStatus;
  children?: ReactNode;
  requestId?: string | null;
  onRetry?: () => void;
  label?: string;
}>) {
  if (props.status === 'ready') return <>{props.children}</>;
  const nonBlocking = props.status === 'refreshing' || props.status === 'unknown-enum';
  return (
    <section
      aria-label={props.label ?? '工作台区域状态'}
      aria-live={nonBlocking ? 'polite' : 'assertive'}
      data-region-status={props.status}
      style={{ minHeight: 96, border: '1px solid #d9e2e1', borderRadius: 8, padding: 16, background: '#fff' }}
    >
      <p role={nonBlocking ? 'status' : 'alert'}>{messages[props.status]}</p>
      {props.requestId ? <p>请求编号：{props.requestId}</p> : null}
      {props.onRetry ? <button type="button" onClick={props.onRetry}>重试此区域</button> : null}
      {nonBlocking ? props.children : null}
    </section>
  );
}
