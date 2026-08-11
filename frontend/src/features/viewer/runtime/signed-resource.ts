import type { DomainError } from '../types';

export function isSignedResourceExpired(error: unknown): boolean {
  const candidate = error as Partial<DomainError> | null;
  return String(candidate?.code) === 'SIGNED_URL_EXPIRED'
    || String(candidate?.code) === 'MEDIA_AUTHORIZATION_EXPIRED'
    || candidate?.httpStatus === 401
    || candidate?.httpStatus === 403
    || candidate?.httpStatus === 410;
}

export async function retrySignedResourceOnce<T>(
  initial: () => Promise<T>,
  refresh: () => Promise<T>,
): Promise<T> {
  try {
    return await initial();
  } catch (error) {
    if (!isSignedResourceExpired(error)) throw error;
    return refresh();
  }
}
