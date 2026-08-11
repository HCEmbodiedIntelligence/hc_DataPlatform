import type { DomainError } from '../api/domain-error';

export function ErrorPanel({ error, onRetry }: { error: DomainError; onRetry?: () => void }) {
  return (
    <section className="error-panel" role="alert">
      <h2>无法加载此区域</h2>
      <p>{error.message}</p>
      {error.requestId ? <p>请求 ID：<code>{error.requestId}</code></p> : null}
      {onRetry ? <button type="button" onClick={onRetry}>重试</button> : null}
    </section>
  );
}
