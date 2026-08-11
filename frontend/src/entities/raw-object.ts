import type { BlockedReason, Brand, DecimalString, UnknownEnum } from './data-source';

export type RawObjectId = Brand<string, 'RawObjectId'>;
export type UploadPartId = Brand<string, 'UploadPartId'>;
export type Sha256 = Brand<string, 'Sha256'>;

export interface RawObject {
  readonly id: RawObjectId;
  readonly relativePath: string;
  readonly sourceRole: string;
  readonly mediaType: string | null;
  readonly sizeBytes: DecimalString;
  readonly multipartStatus: string | UnknownEnum;
  readonly completedParts: DecimalString;
  readonly totalParts: DecimalString | null;
  /** Provider multipart identity. It is not a content digest. */
  readonly multipartEtag: string | null;
  /** Content integrity digest; never inferred from multipartEtag. */
  readonly declaredSha256: Sha256 | null;
  readonly verifiedSha256: Sha256 | null;
  readonly checksumStatus: string | UnknownEnum;
  readonly verificationStatus: string | UnknownEnum;
  readonly resourceVersion: DecimalString;
  readonly updatedAt: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly BlockedReason[];
}

export interface UploadPart {
  readonly id: UploadPartId;
  readonly objectId: RawObjectId;
  readonly partNumber: DecimalString;
  readonly attemptCount: DecimalString;
  readonly offsetBytes: DecimalString;
  readonly sizeBytes: DecimalString;
  readonly status: string | UnknownEnum;
  readonly multipartEtag: string | null;
  readonly checksum: {
    readonly algorithm: string;
    readonly value: string;
    readonly status: string;
  } | null;
  readonly lastError: { readonly code: string; readonly message: string; readonly retryable: boolean } | null;
  readonly updatedAt: string;
  readonly resourceVersion: DecimalString;
}
