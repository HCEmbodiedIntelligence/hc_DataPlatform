import { useEffect, useRef } from 'react';
import type { BlockedReason } from '../../../entities/data-source';
import { formText } from '../../../features/ingest/form-data';

export function DangerousUploadActionDialog(props: {
  readonly open: boolean;
  readonly title: string;
  readonly uploadId: string;
  readonly impact: string;
  readonly blockedReasons: readonly BlockedReason[];
  readonly pending: boolean;
  readonly onClose: () => void;
  readonly onConfirm: (reason: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (props.open && !ref.current?.open) ref.current?.showModal(); if (!props.open && ref.current?.open) ref.current.close(); }, [props.open]);
  return <dialog ref={ref} onClose={props.onClose} aria-labelledby="danger-action-title"><form onSubmit={(event) => { event.preventDefault(); props.onConfirm(formText(new FormData(event.currentTarget), 'reason')); }}><h2 id="danger-action-title">{props.title}</h2><div data-component="ConfirmDialog"><p>稳定 Upload ID：<code>{props.uploadId}</code></p><p>影响摘要：{props.impact}</p>{props.blockedReasons.length ? <ul>{props.blockedReasons.map((item) => <li key={item.code}>{item.code}：{item.message}</li>)}</ul> : null}<label>确认原因<textarea name="reason" required minLength={3} /></label><div className="dialog-actions"><button type="button" onClick={props.onClose}>取消</button><button type="submit" disabled={props.pending || props.blockedReasons.length > 0}>{props.pending ? '提交中…' : '确认执行'}</button></div></div></form></dialog>;
}
