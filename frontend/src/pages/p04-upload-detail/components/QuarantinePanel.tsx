import type { QuarantineSummary } from '../../../features/ingest/upload/model';

export function QuarantinePanel(props: { readonly quarantine: QuarantineSummary | null; readonly onRequestRelease: () => void; readonly canRequestRelease: boolean }) {
  if (!props.quarantine) return <p>当前会话没有隔离事实。</p>;
  const disposition = typeof props.quarantine.disposition === 'string' ? props.quarantine.disposition : `UNKNOWN (${props.quarantine.disposition.raw})`;
  return <article className="quarantine-card"><h3>隔离区</h3><dl><dt>隔离 ID</dt><dd><code>{props.quarantine.quarantineId}</code></dd><dt>处置状态</dt><dd>{disposition}</dd><dt>原因</dt><dd>{props.quarantine.reasonCode}：{props.quarantine.safeSummary}</dd><dt>保留至</dt><dd><time dateTime={props.quarantine.retainUntil}>{props.quarantine.retainUntil}</time></dd></dl><p>隔离对象不会获得普通内容 URL。释放只能通过同一对象集复验和原子可用性提交完成。</p><button type="button" disabled={!props.canRequestRelease} onClick={props.onRequestRelease}>复验并申请释放</button></article>;
}
