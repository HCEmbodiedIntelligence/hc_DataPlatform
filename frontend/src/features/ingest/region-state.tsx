import type { ReactNode } from 'react';

export type IngestRegionState =
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
  | 'offline-reconnecting'
  | 'contract-mismatch'
  | 'unknown-enum'
  | 'feature-unavailable';

const COPY: Record<Exclude<IngestRegionState, 'ready'>, string> = {
  'first-loading': '正在加载…',
  refreshing: '正在刷新，当前显示上次成功的数据。',
  empty: '当前作用域暂无数据。',
  'filtered-empty': '当前筛选没有结果。',
  'partial-error': '该区域暂时不可用，页面其余区域仍可使用。',
  'fatal-error': '页面加载失败，请使用请求 ID 联系支持。',
  forbidden: '你没有读取此资源的权限。',
  'not-found': '资源不存在或已不属于当前作用域。',
  gone: '资源已过期，不能继续原操作。',
  conflict: '资源已经变化，请查看最新事实后重新确认。',
  'rate-limited': '请求过于频繁，请在服务端指定时间后重试。',
  'offline-reconnecting': '网络已断开，缓存数据只读；正在重连。',
  'contract-mismatch': '响应未通过安全合同校验，已阻止该区域。',
  'unknown-enum': '服务端返回了新状态；当前仅提供只读展示。',
  'feature-unavailable': '该功能尚未在当前环境启用。',
};

export function IngestRegion(props: {
  readonly state: IngestRegionState;
  readonly children?: ReactNode;
  readonly requestId?: string | null;
  readonly onRetry?: () => void;
  readonly onClearFilters?: () => void;
  readonly label: string;
}) {
  if (props.state === 'ready' || props.state === 'refreshing') {
    return (
      <section aria-label={props.label} aria-busy={props.state === 'refreshing'} data-region-state={props.state}>
        {props.state === 'refreshing' ? <p role="status">{COPY.refreshing}</p> : null}
        {props.children}
      </section>
    );
  }
  return (
    <section aria-label={props.label} aria-busy={props.state === 'first-loading'} data-region-state={props.state}>
      <p role={props.state.includes('error') || props.state === 'contract-mismatch' ? 'alert' : 'status'}>{COPY[props.state]}</p>
      {props.requestId ? <p>请求 ID：<code>{props.requestId}</code></p> : null}
      {props.onRetry ? <button type="button" onClick={props.onRetry}>重试</button> : null}
      {props.state === 'filtered-empty' && props.onClearFilters ? <button type="button" onClick={props.onClearFilters}>清除筛选</button> : null}
    </section>
  );
}
