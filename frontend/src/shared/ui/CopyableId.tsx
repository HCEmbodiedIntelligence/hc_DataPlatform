import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

export interface CopyableIdProps {
  value: string;
  label?: string;
}

export function CopyableId({ value, label = 'ID' }: CopyableIdProps) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      globalThis.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  };
  const Icon = copied ? Check : Copy;
  return (
    <span className="copyable-id">
      <code>{value}</code>
      <button className="icon-button" type="button" title={`复制${label}`} aria-label={`复制${label}`} onClick={() => void copy()}>
        <Icon aria-hidden="true" />
      </button>
      <span className="sr-only" aria-live="polite">{copied ? `${label}已复制` : ''}</span>
    </span>
  );
}
