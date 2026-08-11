import type { ReactNode } from 'react';

export type StorageRegionStatus =
  | 'ready' | 'first-loading' | 'refreshing' | 'empty' | 'filtered-empty'
  | 'partial-error' | 'fatal-error' | 'forbidden' | 'not-found-gone'
  | 'conflict' | 'rate-limited' | 'offline-reconnecting' | 'contract-mismatch'
  | 'unknown-enum' | 'feature-unavailable';

const copy: Record<Exclude<StorageRegionStatus, 'ready'>, string> = {
  'first-loading': '正在加载存储快照',
  refreshing: '正在刷新；当前继续显示同一作用域的上次成功快照',
  empty: '当前快照没有存储对象',
  'filtered-empty': '当前筛选没有对象，筛选条件已保留',
  'partial-error': '此存储区域暂时不可用，其他快照区域仍可查看',
  'fatal-error': '存储概览暂时无法加载',
  forbidden: '你没有查看此存储投影的权限',
  'not-found-gone': '存储快照或对象不存在，或已经过期',
  conflict: '对象详情与列表快照冲突，请刷新列表后重新打开',
  'rate-limited': '存储服务请求受限，请按服务端提示稍后重试',
  'offline-reconnecting': '网络离线；当前快照只读且可能已过期',
  'contract-mismatch': '存储响应不符合合同，此区域已安全阻断',
  'unknown-enum': '发现服务端新枚举，已按只读未知状态展示',
  'feature-unavailable': '此只读能力尚未在当前环境开放',
};

export function StorageRegionState(props: Readonly<{
  status: StorageRegionStatus;
  children?: ReactNode;
  label?: string;
  requestId?: string | null;
  onRetry?: () => void;
}>) {
  if (props.status === 'ready') return <>{props.children}</>;
  const nonBlocking = props.status === 'refreshing' || props.status === 'unknown-enum';
  return (
    <section aria-label={props.label ?? '存储区域状态'} aria-live={nonBlocking ? 'polite' : 'assertive'} data-region-status={props.status} style={{ minHeight: 96, padding: 16, border: '1px solid #d7e2e0', borderRadius: 8, background: '#fff' }}>
      <p role={nonBlocking ? 'status' : 'alert'}>{copy[props.status]}</p>
      {props.requestId ? <p>请求编号：{props.requestId}</p> : null}
      {props.onRetry ? <button type="button" onClick={props.onRetry}>重试此区域</button> : null}
      {nonBlocking ? props.children : null}
    </section>
  );
}
