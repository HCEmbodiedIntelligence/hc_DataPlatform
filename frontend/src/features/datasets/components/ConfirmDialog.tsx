import { useId } from 'react';
import type { ReactNode } from 'react';

export type ConfirmDialogProps = Readonly<{
  open: boolean;
  title: string;
  resourceId: string;
  description: string;
  impact: readonly string[];
  blockedReasons?: readonly string[];
  confirmLabel: string;
  confirmDisabled?: boolean;
  submitting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: ReactNode;
}>;

export function ConfirmDialog({
  open,
  title,
  resourceId,
  description,
  impact,
  blockedReasons = [],
  confirmLabel,
  confirmDisabled,
  submitting,
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  if (!open) return null;
  return (
    <div
      className="dataset-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !submitting) onCancel();
      }}
    >
      <section
        className="dataset-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <header>
          <p className="dataset-eyebrow">危险操作确认</p>
          <h2 id={titleId}>{title}</h2>
        </header>
        <p id={descriptionId}>{description}</p>
        <dl className="dataset-dialog-identity">
          <dt>稳定资源 ID</dt>
          <dd>
            <code>{resourceId}</code>
          </dd>
        </dl>
        <div>
          <h3>级联影响摘要</h3>
          <ul>
            {impact.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        {blockedReasons.length > 0 ? (
          <div className="dataset-blocked-reasons" role="alert">
            <h3>当前阻断原因</h3>
            <ul>
              {blockedReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {children}
        <footer>
          <button
            type="button"
            className="dataset-button dataset-button--secondary"
            disabled={submitting}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            type="button"
            className="dataset-button dataset-button--danger"
            disabled={confirmDisabled || submitting || blockedReasons.length > 0}
            onClick={onConfirm}
          >
            {submitting ? '提交中…' : confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}
