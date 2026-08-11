import type { FormEvent } from 'react';

export function CreateDatasetDialog({
  open,
  name,
  description,
  labels,
  pending,
  error,
  onNameChange,
  onDescriptionChange,
  onLabelsChange,
  onSubmit,
  onCancel,
}: Readonly<{
  open: boolean;
  name: string;
  description: string;
  labels: string;
  pending: boolean;
  error?: string;
  onNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onLabelsChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
}>) {
  if (!open) return null;
  return (
    <div className="dataset-dialog-backdrop" role="presentation">
      <form
        className="dataset-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-dataset-title"
        onSubmit={onSubmit}
      >
        <p className="dataset-eyebrow">Empty dataset</p>
        <h2 id="create-dataset-title">创建数据集</h2>
        <p>只创建空 Dataset，不隐式创建 Version；同一意图始终复用一个 Idempotency-Key。</p>
        <label>
          名称
          <input
            required
            maxLength={256}
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
          />
        </label>
        <label>
          描述
          <textarea
            maxLength={4096}
            value={description}
            onChange={(event) => onDescriptionChange(event.target.value)}
          />
        </label>
        <label>
          标签（逗号分隔）
          <input value={labels} onChange={(event) => onLabelsChange(event.target.value)} />
        </label>
        {error ? <p role="alert">{error}</p> : null}
        <footer>
          <button
            type="button"
            className="dataset-button dataset-button--secondary"
            disabled={pending}
            onClick={onCancel}
          >
            取消
          </button>
          <button className="dataset-button" disabled={pending || !name.trim()}>
            {pending ? '提交中…' : '创建'}
          </button>
        </footer>
      </form>
    </div>
  );
}
