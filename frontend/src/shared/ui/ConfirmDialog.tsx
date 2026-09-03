import { useEffect, useId, useRef } from 'react';
import { AlertTriangle } from 'lucide-react';
import { trapTabKey } from './focus-trap';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  impact: string;
  resourceId: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  pending?: boolean;
}

export function ConfirmDialog({ open, title, impact, resourceId, confirmLabel, onConfirm, onCancel, pending = false }: ConfirmDialogProps) {
  const id = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    return () => previousFocus.current?.focus();
  }, [open]);
  if (!open) return null;
  return (
    <div className="dialog-backdrop">
      <section ref={dialogRef} className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-impact`} onKeyDown={(event) => {
        trapTabKey(event, dialogRef);
        if (event.key === 'Escape' && !pending) onCancel();
      }}>
        <AlertTriangle aria-hidden="true" />
        <h2 id={`${id}-title`}>{title}</h2>
        <p>资源 ID：<code>{resourceId}</code></p>
        <p id={`${id}-impact`}><strong>影响：</strong>{impact}</p>
        <div className="dialog-actions">
          <button ref={cancelRef} type="button" disabled={pending} onClick={onCancel}>取消</button>
          <button className="button-danger" type="button" disabled={pending} onClick={onConfirm}>{pending ? '提交中…' : confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}
