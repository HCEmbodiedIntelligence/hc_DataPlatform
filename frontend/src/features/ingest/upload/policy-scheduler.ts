export interface UploadRuntimePolicy {
  readonly concurrency: { readonly minimum: number; readonly preferred: number; readonly maximum: number };
  readonly retry: { readonly maxAttemptsPerPart: number; readonly baseDelayMs: number; readonly maximumDelayMs: number; readonly jitterRatio: number; readonly retryableHttpStatuses: readonly number[] };
}

export function effectiveUploadConcurrency(policy: UploadRuntimePolicy, environmentSafeCeiling: number): number {
  if (!Number.isInteger(environmentSafeCeiling) || environmentSafeCeiling < 1) throw new Error('INVALID_ENVIRONMENT_SAFE_CEILING');
  const { minimum, preferred, maximum } = policy.concurrency;
  if (![minimum, preferred, maximum].every((value) => Number.isInteger(value) && value > 0) || minimum > preferred || preferred > maximum) {
    throw new Error('INVALID_UPLOAD_POLICY');
  }
  return Math.min(maximum, Math.max(minimum, Math.min(preferred, environmentSafeCeiling)));
}

export function uploadRetryDelayMs(policy: UploadRuntimePolicy, attempt: number, randomUnit = Math.random()): number {
  if (!Number.isInteger(attempt) || attempt < 1 || attempt > policy.retry.maxAttemptsPerPart) throw new Error('RETRY_ATTEMPT_OUT_OF_POLICY');
  if (randomUnit < 0 || randomUnit > 1) throw new Error('INVALID_RANDOM_UNIT');
  const base = Math.min(policy.retry.maximumDelayMs, policy.retry.baseDelayMs * 2 ** (attempt - 1));
  const spread = policy.retry.jitterRatio;
  return Math.round(base * (1 - spread + 2 * spread * randomUnit));
}

export function isPolicyRetryableStatus(policy: UploadRuntimePolicy, status: number): boolean {
  return policy.retry.retryableHttpStatuses.includes(status);
}
