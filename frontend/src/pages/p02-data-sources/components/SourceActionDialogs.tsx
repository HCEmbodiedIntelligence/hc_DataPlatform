import { useEffect, useRef } from 'react';
import type { BlockedReason } from '../../../entities/data-source';
import { formRawText, formText } from '../../../features/ingest/form-data';

function Modal(props: { readonly open: boolean; readonly title: string; readonly children: React.ReactNode; readonly onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (props.open && !ref.current?.open) ref.current?.showModal();
    if (!props.open && ref.current?.open) ref.current.close();
  }, [props.open]);
  return <dialog ref={ref} onClose={props.onClose}><h2>{props.title}</h2>{props.children}</dialog>;
}

export function ConfirmSourceStateDialog(props: {
  readonly open: boolean;
  readonly sourceId: string;
  readonly action: 'enable' | 'disable';
  readonly pending: boolean;
  readonly blockedReasons: readonly BlockedReason[];
  readonly onConfirm: () => void;
  readonly onClose: () => void;
}) {
  return (
    <Modal open={props.open} title={props.action === 'enable' ? '确认启用数据源' : '确认停用数据源'} onClose={props.onClose}>
      <p>稳定 ID：<code>{props.sourceId}</code></p>
      <p>影响：{props.action === 'disable' ? '阻止创建新的上传会话；历史上传事实保留。' : '允许符合策略的新上传会话。'}</p>
      {props.blockedReasons.length ? <ul>{props.blockedReasons.map((item) => <li key={item.code}>{item.message}</li>)}</ul> : null}
      <div className="dialog-actions"><button type="button" onClick={props.onClose}>取消</button><button type="button" disabled={props.pending || props.blockedReasons.length > 0} onClick={props.onConfirm}>确认</button></div>
    </Modal>
  );
}

/** Future DELETE is fail-closed; if the server ever grants DELETE this ConfirmDialog is mandatory. */
export function ConnectorDeleteConfirmDialog(props: {
  readonly open: boolean;
  readonly sourceId: string;
  readonly blockedReasons: readonly BlockedReason[];
  readonly onClose: () => void;
}) {
  return (
    <Modal open={props.open} title="确认删除连接器" onClose={props.onClose}>
      <div data-component="ConfirmDialog">
        <p>稳定 ID：<code>{props.sourceId}</code></p>
        <p>影响：删除会阻止新会话且可能影响引用；历史上传事实不会被乐观移除。</p>
        <ul>{props.blockedReasons.map((item) => <li key={item.code}>{item.code}：{item.message}</li>)}<li>CONTRACT_NOT_FROZEN：后端尚未提供删除合同，当前禁止提交。</li></ul>
        <div className="dialog-actions"><button type="button" onClick={props.onClose}>取消</button><button type="button" disabled>确认删除</button></div>
      </div>
    </Modal>
  );
}

export function CredentialRotationDialog(props: {
  readonly open: boolean;
  readonly sourceId: string;
  readonly pending: boolean;
  readonly onClose: () => void;
  readonly onConfirm: (token: string, reason: string) => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  return (
    <Modal open={props.open} title="轮换数据源凭据" onClose={props.onClose}>
      <form ref={formRef} onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const token = formRawText(data, 'credentialToken');
        const reason = formText(data, 'reason');
        props.onConfirm(token, reason);
        const secret = event.currentTarget.elements.namedItem('credentialToken');
        if (secret instanceof HTMLInputElement) secret.value = '';
      }}>
        <p>稳定 ID：<code>{props.sourceId}</code></p>
        <p>当前凭据仅显示“已配置”和掩码；新 Token 不会回显、缓存、持久化或进入遥测。</p>
        <label>新 Token<input name="credentialToken" type="password" required autoComplete="new-password" data-sensitive="credential" /></label>
        <label>轮换原因<textarea name="reason" required maxLength={500} /></label>
        <div className="dialog-actions"><button type="button" disabled={props.pending} onClick={() => { formRef.current?.reset(); props.onClose(); }}>取消</button><button type="submit" disabled={props.pending}>{props.pending ? '提交中…' : '确认轮换'}</button></div>
      </form>
    </Modal>
  );
}
