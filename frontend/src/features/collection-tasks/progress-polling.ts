import { isDomainError } from "../../shared/api/domain-error";

export const idleProgressInterval = 60_000;

/** Keep live work responsive without repeatedly reading unchanged or missing resources. */
export function progressRefreshInterval(
  processing: boolean,
  error: unknown,
): number | false {
  if (isDomainError(error)) {
    if (!error.retryable || [401, 403, 404, 410].includes(error.httpStatus ?? 0))
      return false;
    return Math.max(idleProgressInterval, (error.retryAfterSeconds ?? 0) * 1000);
  }
  if (error) return idleProgressInterval;
  return processing ? 5_000 : idleProgressInterval;
}
