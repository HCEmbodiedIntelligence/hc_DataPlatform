export interface RelativeTimeProps {
  value: string | Date;
  now?: number;
}

function formatRelative(timestamp: number, now: number): string {
  const seconds = Math.round((timestamp - now) / 1_000);
  const absolute = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });
  if (absolute < 60) return formatter.format(seconds, 'second');
  if (absolute < 3_600) return formatter.format(Math.round(seconds / 60), 'minute');
  if (absolute < 86_400) return formatter.format(Math.round(seconds / 3_600), 'hour');
  return formatter.format(Math.round(seconds / 86_400), 'day');
}

export function RelativeTime({ value, now = Date.now() }: RelativeTimeProps) {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return <span>未知时间</span>;
  return <time dateTime={date.toISOString()} title={date.toLocaleString('zh-CN')}>{formatRelative(date.getTime(), now)}</time>;
}
