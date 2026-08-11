import type { ReactNode } from 'react';

export type AuditRegionStatus =
  | 'ready' | 'first-loading' | 'refreshing' | 'empty' | 'filtered-empty'
  | 'partial-error' | 'fatal-error' | 'forbidden' | 'not-found-gone'
  | 'conflict' | 'rate-limited' | 'offline-reconnecting' | 'contract-mismatch'
  | 'unknown-enum' | 'feature-unavailable';

const messages: Record<Exclude<AuditRegionStatus, 'ready'>, string> = {
  'first-loading': '正在加载授权后的审计投影',
  refreshing: '正在获取新快照；当前快照保持只读',
  empty: '此时间范围没有可见审计事件',
  'filtered-empty': '当前筛选没有结果，筛选条件已保留',
  'partial-error': '此审计区域暂时不可用，其他成功区域保持可见',
  'fatal-error': '审计日志暂时无法加载',
  forbidden: '你没有审计读取权限；页面不会请求事件详情',
  'not-found-gone': '事件不可见、不存在或已按保留策略到期',
  conflict: '审计快照或授权已变化，请从首组重新加载',
  'rate-limited': '审计请求受限，请按服务端提示稍后重试',
  'offline-reconnecting': '网络离线；现有快照只读且可能已过期',
  'contract-mismatch': '审计投影不符合合同，受影响区域已安全阻断',
  'unknown-enum': '存在未登记事件或新枚举，已禁用相关操作并只读展示',
  'feature-unavailable': '服务端合同尚未冻结，此功能当前不可用',
};

export function AuditRegionState(props: Readonly<{
  status: AuditRegionStatus;
  children?: ReactNode;
  label?: string;
  requestId?: string | null;
  onRetry?: () => void;
}>) {
  if (props.status === 'ready') return <>{props.children}</>;
  const nonBlocking = props.status === 'refreshing' || props.status === 'unknown-enum';
  return (
    <section aria-label={props.label ?? '审计区域状态'} aria-live={nonBlocking ? 'polite' : 'assertive'} data-region-status={props.status} style={{ minHeight: 96, padding: 16, border: '1px solid #d7e2e0', borderRadius: 8, background: '#fff' }}>
      <p role={nonBlocking ? 'status' : 'alert'}>{messages[props.status]}</p>
      {props.requestId ? <p>请求编号：{props.requestId}</p> : null}
      {props.onRetry ? <button type="button" onClick={props.onRetry}>重试此区域</button> : null}
      {nonBlocking ? props.children : null}
    </section>
  );
}
