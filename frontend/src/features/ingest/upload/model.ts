import type { BlockedReason, Brand, DecimalString, IngestScope, UnknownEnum } from '../../../entities/data-source';
import type { RawObject } from '../../../entities/raw-object';
import type { UploadJob } from '../../../entities/upload-job';
import type { QuarantineDisposition, ValidationStage } from '../validation-pipeline';

export type UploadId = Brand<string, 'UploadId'>;
export type UploadETag = Brand<string, 'UploadETag'>;
export type UploadLifecycleStatus =
  | 'CREATED' | 'AUTHORIZING' | 'UPLOADING' | 'PAUSED' | 'FINALIZING'
  | 'PENDING_VERIFY' | 'VERIFYING' | 'AVAILABLE' | 'FAILED' | 'QUARANTINED'
  | 'CANCELLING' | 'CANCELLED' | 'EXPIRED' | UnknownEnum;
export type VerificationStatus =
  | 'NOT_STARTED' | 'QUEUED' | 'RUNNING' | 'PASSED' | 'FAILED' | 'CANCELLED' | UnknownEnum;

export type UploadAllowedAction =
  | 'VIEW' | 'PAUSE' | 'RESUME' | 'RETRY_UPLOAD' | 'SUBMIT_MANIFEST'
  | 'RETRY_VERIFY' | 'CREATE_REPLACEMENT' | 'CANCEL';

export interface UploadProgress {
  readonly expectedBytes: DecimalString | null;
  readonly confirmedReceivedBytes: DecimalString;
  readonly completedParts: DecimalString;
  readonly totalParts: DecimalString | null;
  readonly completedObjects: DecimalString;
  readonly totalObjects: DecimalString;
  readonly throughputBytesPerSecond: DecimalString | null;
  readonly estimatedRemainingSeconds: DecimalString | null;
  readonly verificationStage: string | null;
}

export interface UploadSession {
  readonly uploadId: UploadId;
  readonly scope: IngestScope;
  readonly dataSource: {
    readonly id: string;
    readonly name: string;
    readonly sourceType: string;
    readonly sourceFormat: string;
    readonly configurationVersion: DecimalString;
    readonly credentialVersion: DecimalString;
    readonly uploadPolicyVersion: DecimalString;
  };
  readonly targetDataset: { readonly id: string; readonly name: string } | null;
  readonly result: {
    readonly datasetId: string;
    readonly datasetVersionId: string;
    readonly datasetVersionStatus: string;
  } | null;
  readonly supersedesUploadId: UploadId | null;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly adapterVersion: string;
  readonly lifecycleStatus: UploadLifecycleStatus;
  readonly verificationStatus: VerificationStatus;
  readonly progress: UploadProgress;
  readonly latestVerificationRunId: string | null;
  readonly activeJobIds: readonly string[];
  readonly createdBy: { readonly id: string; readonly displayName: string };
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly etag: UploadETag;
  readonly resourceVersion: DecimalString;
  readonly allowedActions: readonly UploadAllowedAction[];
  readonly blockedReasons: readonly BlockedReason[];
}

export interface VerificationRun {
  readonly verificationRunId: string;
  readonly supersedesRunId: string | null;
  readonly objectSetHash: string;
  readonly manifestSha256: string;
  readonly adapterVersion: string;
  readonly status: VerificationStatus;
  readonly stages: readonly ValidationStage[];
  readonly jobId: string;
  readonly createdAt: string;
  readonly resourceVersion: DecimalString;
}

export interface QuarantineSummary {
  readonly quarantineId: string;
  readonly uploadId: UploadId;
  readonly verificationRunId: string;
  readonly reasonCode: string;
  readonly safeSummary: string;
  readonly disposition: QuarantineDisposition;
  readonly retainUntil: string;
  readonly createdAt: string;
}

export interface UploadBootstrap {
  readonly session: UploadSession;
  readonly objects: readonly RawObject[];
  readonly latestVerificationRun: VerificationRun | null;
  readonly latestQuarantine: QuarantineSummary | null;
  readonly activeJobs: readonly UploadJob[];
  readonly requestId: string;
  readonly contractVersion: string;
}

export function compareResourceVersion(a: string, b: string): number {
  const left = BigInt(a);
  const right = BigInt(b);
  return left === right ? 0 : left > right ? 1 : -1;
}

export function acceptNewerResource<T extends { readonly resourceVersion: string }>(current: T, incoming: T): T {
  return compareResourceVersion(incoming.resourceVersion, current.resourceVersion) > 0 ? incoming : current;
}

export function isUploadTerminal(status: UploadLifecycleStatus): boolean {
  return typeof status === 'string' && ['AVAILABLE', 'FAILED', 'QUARANTINED', 'CANCELLED', 'EXPIRED'].includes(status);
}
