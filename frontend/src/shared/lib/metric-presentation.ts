type IntegerMetric = string | number | bigint | null | undefined;

const NS_PER_MILLISECOND = 1_000_000n;
const NS_PER_SECOND = 1_000_000_000n;
const SECONDS_PER_MINUTE = 60n;
const SECONDS_PER_HOUR = 60n * SECONDS_PER_MINUTE;
const SECONDS_PER_DAY = 24n * SECONDS_PER_HOUR;

function parseInteger(value: IntegerMetric): bigint | null {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number') {
    return Number.isSafeInteger(value) ? BigInt(value) : null;
  }
  if (typeof value !== 'string' || !/^-?\d+$/u.test(value)) return null;
  return BigInt(value);
}

function parseNonNegativeInteger(value: IntegerMetric): bigint | null {
  const parsed = parseInteger(value);
  return parsed !== null && parsed >= 0n ? parsed : null;
}

function roundedDivision(value: bigint, divisor: bigint): bigint {
  return (value + divisor / 2n) / divisor;
}

export function formatEffectiveDuration(value: IntegerMetric): string {
  const nanoseconds = parseNonNegativeInteger(value);
  if (nanoseconds === null) return '未知';
  if (nanoseconds === 0n) return '0 秒';

  if (nanoseconds < NS_PER_SECOND) {
    const milliseconds = roundedDivision(nanoseconds, NS_PER_MILLISECOND);
    return milliseconds === 0n ? '< 1 毫秒' : `${milliseconds.toString()} 毫秒`;
  }

  if (nanoseconds < SECONDS_PER_MINUTE * NS_PER_SECOND) {
    const tenths = roundedDivision(nanoseconds * 10n, NS_PER_SECOND);
    const wholeSeconds = tenths / 10n;
    const decimal = tenths % 10n;
    return `${wholeSeconds.toString()}${decimal === 0n ? '' : `.${decimal.toString()}`} 秒`;
  }

  const totalSeconds = roundedDivision(nanoseconds, NS_PER_SECOND);
  const days = totalSeconds / SECONDS_PER_DAY;
  const hours = (totalSeconds % SECONDS_PER_DAY) / SECONDS_PER_HOUR;
  const minutes = (totalSeconds % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE;
  const seconds = totalSeconds % SECONDS_PER_MINUTE;

  if (days > 0n) {
    return `${days.toString()} 天${hours > 0n ? ` ${hours.toString()} 小时` : ''}`;
  }
  if (hours > 0n) {
    return `${hours.toString()} 小时${minutes > 0n ? ` ${minutes.toString()} 分` : ''}`;
  }
  return `${minutes.toString()} 分${seconds > 0n ? ` ${seconds.toString()} 秒` : ''}`;
}

export function formatSignedDuration(value: IntegerMetric): string {
  const nanoseconds = parseInteger(value);
  if (nanoseconds === null) return '未知';
  const absolute = nanoseconds < 0n ? -nanoseconds : nanoseconds;
  return `${nanoseconds < 0n ? '−' : ''}${formatEffectiveDuration(absolute)}`;
}

export function formatTimeRange(startNs: IntegerMetric, endNs: IntegerMetric): string {
  const start = parseNonNegativeInteger(startNs);
  const end = parseNonNegativeInteger(endNs);
  if (start === null || end === null || start >= end) return '范围未知';
  return `[${formatEffectiveDuration(start)}, ${formatEffectiveDuration(end)})`;
}

export function formatStorageSize(value: IntegerMetric): string {
  const bytes = parseNonNegativeInteger(value);
  if (bytes === null) return '未知';

  const units = ['KB', 'MB', 'GB', 'TB', 'PB', 'EB'] as const;
  let divisor = 1024n;
  let unitIndex = 0;
  while (bytes >= divisor * 1024n && unitIndex < units.length - 1) {
    divisor *= 1024n;
    unitIndex += 1;
  }

  const tenths = roundedDivision(bytes * 10n, divisor);
  if (bytes > 0n && tenths === 0n) return `< 0.1 ${units[unitIndex]}`;
  const whole = tenths / 10n;
  const decimal = tenths % 10n;
  return `${whole.toString()}${decimal === 0n ? '' : `.${decimal.toString()}`} ${units[unitIndex]}`;
}

export function nanosecondsToSecondsInput(value: IntegerMetric): string {
  const nanoseconds = parseNonNegativeInteger(value);
  if (nanoseconds === null) return '';
  const whole = nanoseconds / NS_PER_SECOND;
  const fraction = (nanoseconds % NS_PER_SECOND).toString().padStart(9, '0').replace(/0+$/u, '');
  return `${whole.toString()}${fraction ? `.${fraction}` : ''}`;
}

export function secondsInputToNanoseconds(value: string): string | null {
  const normalized = value.trim();
  if (!/^\d+(?:\.\d{1,9})?$/u.test(normalized)) return null;
  const [whole = '0', fraction = ''] = normalized.split('.');
  return (BigInt(whole) * NS_PER_SECOND + BigInt(fraction.padEnd(9, '0'))).toString();
}
