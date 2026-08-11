import type { UploadId } from './model';

/** Coalesces concurrent part-expiry signals into one authorization renewal per upload. */
export class AuthorizationRefreshCoordinator {
  #active = new Map<UploadId, Promise<void>>();

  refresh(uploadId: UploadId, renew: () => Promise<void>): Promise<void> {
    const existing = this.#active.get(uploadId);
    if (existing) return existing;
    const created = renew().finally(() => {
      if (this.#active.get(uploadId) === created) this.#active.delete(uploadId);
    });
    this.#active.set(uploadId, created);
    return created;
  }

  clear(): void {
    this.#active.clear();
  }
}
