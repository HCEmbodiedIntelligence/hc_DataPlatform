import type { CalibrationSet, PrecisionDecimal } from '../../entities/calibration';

export const calibrationContractAssumptions = {
  decimalEncoding: 'base-10 string; browser does not round or normalize',
  fallbackSelection: 'an explicit binding wins; inferred selection requires exactly one eligible candidate',
  readyImmutability: 'READY content and content_hash are immutable; changes create a new DRAFT version',
  publishEvidence: 'PASSED validation must match content_hash and validation_context_hash',
} as const;

const decimalPattern = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

export function isPrecisionDecimal(value: string): value is PrecisionDecimal {
  return decimalPattern.test(value);
}

export function validateCovariance(values: readonly string[]): readonly string[] {
  const errors: string[] = [];
  if (values.length !== 36) errors.push('Covariance 必须包含 6×6 共 36 个值。');
  values.forEach((value, index) => {
    if (!isPrecisionDecimal(value)) errors.push(`Covariance[${index}] 不是有效十进制字符串。`);
  });
  return errors;
}

export interface CalibrationValidationEvidence {
  readonly status: 'PASSED' | 'FAILED' | 'STALE' | 'UNKNOWN';
  readonly contentHash: string;
  readonly validationContextHash: string;
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
}

export function canPublishCalibration(
  set: CalibrationSet,
  validation: CalibrationValidationEvidence | null,
): { readonly allowed: boolean; readonly reasons: readonly string[] } {
  const reasons: string[] = [];
  if (set.snapshotStatus !== 'DRAFT') reasons.push('只有 DRAFT 可发布。');
  if (!set.allowedActions.includes('PUBLISH')) reasons.push('资源未声明 PUBLISH action。');
  if (set.blockedReasons.length > 0) reasons.push(...set.blockedReasons.map((reason) => reason.message));
  if (!set.contentHash || !set.validationContextHash) reasons.push('缺少发布所需的内容或上下文 hash。');
  if (!validation || validation.status !== 'PASSED') reasons.push('需要当前 PASSED 校验。');
  if (validation && (validation.contentHash !== set.contentHash || validation.validationContextHash !== set.validationContextHash)) {
    reasons.push('校验结果与当前内容或上下文不一致。');
  }
  if (validation) reasons.push(...validation.blockedReasons.map((reason) => reason.message));
  return { allowed: reasons.length === 0, reasons };
}

export function resolveCalibrationFallback<T extends { readonly id: string }>(
  explicit: T | null,
  eligibleCandidates: readonly T[],
): { readonly kind: 'resolved'; readonly value: T; readonly provenance: 'EXPLICIT' | 'INFERRED' } | { readonly kind: 'blocked'; readonly reason: string } {
  if (explicit) return { kind: 'resolved', value: explicit, provenance: 'EXPLICIT' };
  if (eligibleCandidates.length !== 1) {
    return { kind: 'blocked', reason: eligibleCandidates.length === 0 ? '没有合法标定候选。' : '存在多个候选，禁止选择最新版本。' };
  }
  const value = eligibleCandidates[0];
  return value ? { kind: 'resolved', value, provenance: 'INFERRED' } : { kind: 'blocked', reason: '没有合法标定候选。' };
}

