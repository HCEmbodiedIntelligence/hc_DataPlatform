import { int64ToBigInt, type Int64String } from '../../shared/lib/bigint-string';
import type { StorageMetric } from '../../entities/storage-inventory';

export const STORAGE_METRICS_CONTRACT = {
  capacity: 'physical capacity is the service-provided actual_oss_physical_bytes for one immutable inventory snapshot',
  cost: 'money is an integer minor-unit service fact; the browser never derives cost from bytes',
  reuseRate: 'reuse_rate is a versioned service fact; logical/physical byte division is diagnostic only and is not substituted',
  reconciliation: 'registered, observed OSS, unclassified and multipart bytes remain separate until the backend freezes a signed gap formula',
} as const;

export function formatByteString(value: Int64String): string {
  let scaled = int64ToBigInt(value);
  let divisor = 1n;
  let unitIndex = 0;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'] as const;
  while (scaled >= 1024n && unitIndex < units.length - 1) {
    scaled /= 1024n;
    divisor *= 1024n;
    unitIndex += 1;
  }
  const raw = int64ToBigInt(value);
  const whole = raw / divisor;
  const tenth = ((raw % divisor) * 10n) / divisor;
  return `${whole.toString()}${tenth === 0n ? '' : `.${tenth.toString()}`} ${units[unitIndex]}`;
}

export function displayByteMetric(metric: StorageMetric<Int64String>): string {
  return metric.state === 'KNOWN' ? formatByteString(metric.value) : metric.state;
}

export function displayDecimalMetric(metric: StorageMetric<string>): string {
  if (metric.state !== 'KNOWN') return metric.state;
  const negative = metric.value.startsWith('-');
  const source = negative ? metric.value.slice(1) : metric.value;
  const [whole = '0', fraction = ''] = source.split('.');
  const digits = `${whole}${fraction}`.replace(/^0+(?=\d)/, '');
  const scale = fraction.length;
  const percentNumerator = BigInt(digits || '0') * 100n;
  const base = 10n ** BigInt(scale);
  const percentWhole = percentNumerator / base;
  const percentTenth = ((percentNumerator % base) * 10n) / base;
  return `${negative ? '-' : ''}${percentWhole.toString()}${percentTenth === 0n ? '' : `.${percentTenth.toString()}`}%`;
}

export function displayMoneyMetric(metric: StorageMetric<{ minorUnits: Int64String; currency: string }>): string {
  if (metric.state !== 'KNOWN') return metric.state;
  const raw = int64ToBigInt(metric.value.minorUnits);
  const major = raw / 100n;
  const minor = (raw % 100n).toString().padStart(2, '0');
  return `${metric.value.currency} ${major.toString()}.${minor}`;
}

export function displayMinorUnitMetric(metric: StorageMetric<Int64String>, currency: string): string {
  if (metric.state !== 'KNOWN') return metric.state;
  const raw = int64ToBigInt(metric.value);
  const sign = raw < 0n ? '-' : '';
  const absolute = raw < 0n ? -raw : raw;
  const major = absolute / 100n;
  const minor = (absolute % 100n).toString().padStart(2, '0');
  return `${currency} ${sign}${major.toString()}.${minor}`;
}
