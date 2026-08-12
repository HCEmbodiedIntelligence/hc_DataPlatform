import { Tag } from 'antd';
import { Circle, CircleAlert, CircleCheck, CircleHelp, CircleX, Info } from 'lucide-react';
import type { StatusTone } from './contracts';
import styles from './state.module.css';

const toneConfig = {
  neutral: { color: 'default', Icon: Circle },
  info: { color: 'blue', Icon: Info },
  success: { color: 'success', Icon: CircleCheck },
  warning: { color: 'warning', Icon: CircleAlert },
  danger: { color: 'error', Icon: CircleX },
} as const;

export interface StatusTagProps {
  status: string | null | undefined;
  label?: string;
  tone?: StatusTone;
  known?: boolean;
}

export function StatusTag({ status, label, tone = 'neutral', known }: Readonly<StatusTagProps>) {
  const normalized = status?.trim() || 'UNKNOWN';
  const isKnown = known ?? normalized.toUpperCase() !== 'UNKNOWN';
  const resolvedTone: StatusTone = isKnown ? tone : 'warning';
  const { color, Icon } = isKnown
    ? toneConfig[resolvedTone]
    : { color: 'warning' as const, Icon: CircleHelp };
  const resolvedLabel = isKnown ? (label ?? normalized) : '未知状态';

  return (
    <Tag
      className={styles.statusTag}
      color={color}
      icon={<Icon aria-hidden="true" size={13} />}
      data-status={normalized}
      data-known={String(isKnown)}
      aria-label={`状态：${resolvedLabel}`}
    >
      {resolvedLabel}
    </Tag>
  );
}
