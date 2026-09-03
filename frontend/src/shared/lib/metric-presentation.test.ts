import { describe, expect, it } from 'vitest';
import {
  formatEffectiveDuration,
  formatSignedDuration,
  formatStorageSize,
  formatTimeRange,
  nanosecondsToSecondsInput,
  secondsInputToNanoseconds,
} from './metric-presentation';

describe('metric presentation', () => {
  it('presents nanosecond durations with readable time units', () => {
    expect(formatEffectiveDuration('303566666000')).toBe('5 分 4 秒');
    expect(formatEffectiveDuration('250000000')).toBe('250 毫秒');
    expect(formatEffectiveDuration('1500000000')).toBe('1.5 秒');
    expect(formatEffectiveDuration('7200000000000')).toBe('2 小时');
    expect(formatSignedDuration('-1500000000')).toBe('−1.5 秒');
    expect(formatTimeRange('1000000000', '4200000000')).toBe('[1 秒, 4.2 秒)');
  });

  it('presents byte counts with readable storage units', () => {
    expect(formatStorageSize('143968017')).toBe('137.3 MB');
    expect(formatStorageSize('0')).toBe('0 KB');
    expect(formatStorageSize('1099511627776')).toBe('1 TB');
  });

  it('converts editable seconds without losing nanosecond precision', () => {
    expect(nanosecondsToSecondsInput('1234567890')).toBe('1.23456789');
    expect(secondsInputToNanoseconds('1.234567890')).toBe('1234567890');
    expect(secondsInputToNanoseconds('1.')).toBeNull();
  });

  it('does not invent values for invalid or unavailable facts', () => {
    expect(formatEffectiveDuration('-1')).toBe('未知');
    expect(formatStorageSize(null)).toBe('未知');
    expect(formatTimeRange('2', '1')).toBe('范围未知');
  });
});
