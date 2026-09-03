import type { JSX } from 'react';

export type AnnotationPageStateKind =
  | 'first-loading'
  | 'refreshing'
  | 'empty'
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

const copy: Readonly<Record<AnnotationPageStateKind, string>> = {
  'first-loading': '正在加载标注任务…',
  refreshing: '正在刷新，当前显示的是上一次成功数据。',
  empty: '当前没有任务；若使用了筛选，可以清除筛选后重试。',
  'partial-error': '部分资源加载失败，其他区域仍可使用。',
  'fatal-error': '任务数据无法安全加载。',
  forbidden: '你没有访问此标注资源的权限。',
  'not-found-gone': '任务不存在或已经过期。',
  conflict: '任务或草稿已在其他位置更新，请比较后显式重新应用。',
  'rate-limited': '请求过于频繁，请在服务端指定时间后重试。',
  'offline-reconnecting': '网络已断开，正在重连；写操作已暂停。',
  'contract-mismatch': '服务端响应不符合标注合同，写操作已关闭。',
  'unknown-enum': '检测到当前客户端未知的数据类型，只读显示以避免丢失。',
  'feature-unavailable': '此能力尚未完成授权合同签署或当前部署不可用。',
};

export function AnnotationPageState({
  kind,
  detail,
  problemCode,
  requestId,
  retryable,
  onRetry,
}: {
  kind: AnnotationPageStateKind;
  detail?: string;
  problemCode?: string;
  requestId?: string;
  retryable?: boolean;
  onRetry?: () => void;
}): JSX.Element {
  return (
    <section className={`annotation-page-state annotation-page-state--${kind}`} role={kind.includes('loading') || kind === 'refreshing' ? 'status' : 'alert'} aria-live="polite">
      <p>{copy[kind]}</p>
      {detail ? <p>{detail}</p> : null}
      {problemCode ? <p>问题代码：<code>{problemCode}</code></p> : null}
      {requestId ? <p>请求 ID：<code>{requestId}</code></p> : null}
      {retryable ? <p>服务端允许重试；请确认当前内容后再操作。</p> : null}
      {onRetry ? <button type="button" onClick={onRetry}>{retryable ? '按服务端提示重试' : '重新加载'}</button> : null}
    </section>
  );
}
