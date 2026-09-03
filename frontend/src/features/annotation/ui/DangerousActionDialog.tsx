import { useEffect, useRef } from 'react';
import type { JSX, ReactNode } from 'react';

export interface DangerousActionDialogProps {
  readonly open: boolean;
  readonly title: string;
  readonly stableResourceId: string;
  readonly impact: readonly string[];
  readonly blockedReasons?: readonly string[];
  readonly pending?: boolean;
  readonly confirmLabel: string;
  readonly children?: ReactNode;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}

export function DangerousActionDialog(props: DangerousActionDialogProps): JSX.Element | null {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const onCancelRef = useRef(props.onCancel);
  const pendingRef = useRef(props.pending);
  onCancelRef.current = props.onCancel;
  pendingRef.current = props.pending;
  useEffect(() => {
    if (!props.open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    titleRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || pendingRef.current) return;
      event.preventDefault();
      onCancelRef.current();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [props.open]);

  if (!props.open) return null;
  return (
    <div className="dangerous-dialog-backdrop" role="presentation">
      <section role="dialog" aria-modal="true" aria-labelledby="dangerous-action-title" className="dangerous-dialog">
        <h2 id="dangerous-action-title" ref={titleRef} tabIndex={-1}>{props.title}</h2>
        <p>资源 ID：<code>{props.stableResourceId}</code></p>
        <h3>本次操作影响</h3>
        <ul>{props.impact.map((item) => <li key={item}>{item}</li>)}</ul>
        {props.blockedReasons?.length ? <div role="alert"><h3>当前阻断</h3><ul>{props.blockedReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div> : null}
        {props.children}
        <footer>
          <button type="button" disabled={props.pending} onClick={props.onCancel}>取消</button>
          <button type="button" disabled={props.pending || !!props.blockedReasons?.length} onClick={props.onConfirm}>{props.pending ? '提交中…' : props.confirmLabel}</button>
        </footer>
      </section>
    </div>
  );
}
