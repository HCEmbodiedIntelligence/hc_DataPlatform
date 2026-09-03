export interface ShortUploadAuthorization {
  readonly authorizationId: string;
  readonly issuedAt: string;
  readonly expiresAt: string;
  readonly refreshAfter: string;
  readonly ossRegion: string;
  readonly endpoint: string;
  readonly bucket: string;
  readonly objectPrefix: string;
  readonly credentials: {
    readonly accessKeyId: string;
    readonly accessKeySecret: string;
    readonly securityToken: string;
  };
}

export interface UploadObjectPlanSecret {
  readonly uploadObjectId: string;
  readonly clientObjectId: string;
  readonly relativePath: string;
  readonly sizeBytes: string;
  readonly multipartUploadId: string;
  readonly objectKey: string;
  readonly partSizeBytes: string;
  readonly confirmedParts: readonly { readonly partNumber: string; readonly etag: string; readonly checksumAlgorithm: string; readonly checksumValue: string }[];
}

export interface UploadHandoffSecret extends ShortUploadAuthorization {
  readonly policy: {
    readonly schemaVersion: 1;
    readonly policyVersion: string;
    readonly effectiveUntil: string;
    readonly concurrency: { readonly minimum: number; readonly preferred: number; readonly maximum: number };
    readonly retry: { readonly maxAttemptsPerPart: number; readonly baseDelayMs: number; readonly maximumDelayMs: number; readonly jitterRatio: number; readonly retryableHttpStatuses: readonly number[] };
  } | null;
  readonly objectPlans: readonly UploadObjectPlanSecret[];
}

/** No serialization, inspection, persistence, logging, or DevTools integration. */
export class UploadAuthorizationVault {
  #values = new Map<string, ShortUploadAuthorization | UploadHandoffSecret>();
  #localFiles = new Map<string, readonly File[]>();
  #expiryTimers = new Map<string, ReturnType<typeof setTimeout>>();
  #scopeKey: string | null = null;

  bindScope(scopeKey: string | null): void {
    if (scopeKey !== this.#scopeKey) this.destroy();
    this.#scopeKey = scopeKey;
  }

  replace(uploadId: string, value: ShortUploadAuthorization | UploadHandoffSecret, localFiles: readonly File[] = []): void {
    this.destroy(uploadId);
    this.#values.set(uploadId, value);
    this.#localFiles.set(uploadId, [...localFiles]);
    const remaining = Math.max(0, Date.parse(value.expiresAt) - Date.now());
    this.#expiryTimers.set(uploadId, setTimeout(() => this.destroy(uploadId), Math.min(remaining, 2_147_483_647)));
  }

  use<T>(uploadId: string, consume: (value: ShortUploadAuthorization | UploadHandoffSecret) => T): T {
    const value = this.#values.get(uploadId);
    if (!value) throw new Error('UPLOAD_AUTHORIZATION_UNAVAILABLE');
    return consume(value);
  }

  destroy(uploadId?: string): void {
    if (uploadId) {
      this.#values.delete(uploadId);
      this.#localFiles.delete(uploadId);
      const timer = this.#expiryTimers.get(uploadId);
      if (timer) clearTimeout(timer);
      this.#expiryTimers.delete(uploadId);
      return;
    }
    this.#values.clear();
    this.#localFiles.clear();
    for (const timer of this.#expiryTimers.values()) clearTimeout(timer);
    this.#expiryTimers.clear();
  }

  has(uploadId: string): boolean {
    return this.#values.has(uploadId);
  }

  useLocalFiles<T>(uploadId: string, consume: (files: readonly File[]) => T): T {
    const files = this.#localFiles.get(uploadId);
    if (!files) throw new Error('LOCAL_UPLOAD_FILES_UNAVAILABLE');
    return consume(files);
  }
}

/** The only process-memory vault used by P03's controller and P04's command surface. */
export const ingestUploadAuthorizationVault = new UploadAuthorizationVault();

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => ingestUploadAuthorizationVault.destroy());
}

export class UploadResourceError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
  }
}

export async function withSingleAuthorizationRefresh<T>(options: {
  readonly operation: () => Promise<T>;
  readonly refresh: () => Promise<void>;
  readonly isExpired: (error: unknown) => boolean;
}): Promise<T> {
  try {
    return await options.operation();
  } catch (error) {
    if (!options.isExpired(error)) throw error;
    await options.refresh();
    try {
      return await options.operation();
    } catch (retryError) {
      if (options.isExpired(retryError)) {
        throw new UploadResourceError('SIGNED_URL_REFRESH_FAILED', '上传授权连续过期，资源已进入错误状态');
      }
      throw retryError;
    }
  }
}
