export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export interface StatusBadgeProps {
  status: string;
  label?: string;
  tone?: StatusTone;
}

const symbols: Record<StatusTone, string> = {
  neutral: '•',
  info: '●',
  success: '✓',
  warning: '!',
  danger: '×',
};

export function StatusBadge({ status, label = status, tone = 'neutral' }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone}`} data-status={status}>
      <span aria-hidden="true">{symbols[tone]}</span>
      {label}
    </span>
  );
}
