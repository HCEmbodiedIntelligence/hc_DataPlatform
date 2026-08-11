export function BatchOperationBar(props: {
  readonly count: number;
  readonly pauseDisabled: boolean;
  readonly cancelDisabled: boolean;
  readonly pending: boolean;
  readonly result?: { readonly succeeded: number; readonly failed: number };
  readonly onPause: () => void;
  readonly onCancel: () => void;
  readonly onClear: () => void;
}) {
  if (!props.count) return null;
  return <div className="batch-bar" role="region" aria-label="批量操作"><strong>已选择 {props.count} 项</strong><button type="button" disabled={props.pauseDisabled || props.pending} onClick={props.onPause}>批量暂停</button><button type="button" disabled={props.cancelDisabled || props.pending} onClick={props.onCancel}>批量取消</button><button type="button" disabled={props.pending} onClick={props.onClear}>清除选择</button>{props.pending ? <span role="status">正在逐项提交（每项独立 ETag）…</span> : props.result ? <span role="status">成功 {props.result.succeeded}，失败 {props.result.failed}</span> : null}</div>;
}
