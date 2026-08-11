import type { Brand, DecimalString, UnknownEnum } from './data-source';

export type UploadJobId = Brand<string, 'UploadJobId'>;
export type UploadJobStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | UnknownEnum;

export interface UploadJob {
  readonly id: UploadJobId;
  readonly type: string | UnknownEnum;
  readonly status: UploadJobStatus;
  readonly stage: string | UnknownEnum;
  readonly progress: {
    readonly completed: DecimalString;
    readonly total: DecimalString | null;
    readonly unit: string;
  };
  readonly resourceRef: { readonly resourceType: string; readonly resourceId: string };
  readonly resultRef: Readonly<Record<string, string>> | null;
  readonly safeError: {
    readonly code: string;
    readonly message: string;
    readonly retryable: boolean;
    readonly requestId: string;
  } | null;
  readonly etag: string;
  readonly allowedActions: readonly string[];
  readonly createdAt: string;
  readonly updatedAt: string;
  /** Canonical platform AsyncJob events provide this; ingest command receipts may not. */
  readonly resourceVersion: DecimalString | null;
}

export const TERMINAL_UPLOAD_JOB_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED']);

export function isUploadJobTerminal(job: UploadJob): boolean {
  return typeof job.status === 'string' && TERMINAL_UPLOAD_JOB_STATUSES.has(job.status);
}
