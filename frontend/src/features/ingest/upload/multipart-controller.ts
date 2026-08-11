import type { UploadId } from './model';
import { withSingleAuthorizationRefresh } from './authorization-vault';

export interface UploadPartInput {
  readonly uploadId: UploadId;
  readonly objectId: string;
  readonly partNumber: string;
  readonly bytes: Blob;
  readonly attempt: string;
}

export interface UploadedPartFact {
  readonly partNumber: string;
  readonly sizeBytes: string;
  readonly etag: string;
  readonly checksumAlgorithm: string;
  readonly checksumValue: string;
  readonly attempt: string;
}

export interface MultipartOssPort {
  uploadPart(input: UploadPartInput, signal: AbortSignal): Promise<UploadedPartFact>;
  listParts(uploadId: UploadId, objectId: string, signal: AbortSignal): Promise<readonly UploadedPartFact[]>;
  completeMultipart(uploadId: UploadId, objectId: string, parts: readonly UploadedPartFact[], signal: AbortSignal): Promise<void>;
  abortInFlight(uploadId: UploadId): Promise<void>;
  destroySecrets(): void;
}

export class MultipartController {
  constructor(
    private readonly port: MultipartOssPort,
    private readonly renewAuthorization: (uploadId: UploadId) => Promise<void>,
    private readonly isAuthorizationExpired: (error: unknown) => boolean,
  ) {}

  async uploadPart(input: UploadPartInput, signal: AbortSignal): Promise<UploadedPartFact> {
    return withSingleAuthorizationRefresh({
      operation: () => this.port.uploadPart(input, signal),
      refresh: () => this.renewAuthorization(input.uploadId),
      isExpired: this.isAuthorizationExpired,
    });
  }

  async listParts(uploadId: UploadId, objectId: string, signal: AbortSignal): Promise<readonly UploadedPartFact[]> {
    return withSingleAuthorizationRefresh({
      operation: () => this.port.listParts(uploadId, objectId, signal),
      refresh: () => this.renewAuthorization(uploadId),
      isExpired: this.isAuthorizationExpired,
    });
  }

  async completeMultipart(uploadId: UploadId, objectId: string, parts: readonly UploadedPartFact[], signal: AbortSignal): Promise<void> {
    return withSingleAuthorizationRefresh({
      operation: () => this.port.completeMultipart(uploadId, objectId, parts, signal),
      refresh: () => this.renewAuthorization(uploadId),
      isExpired: this.isAuthorizationExpired,
    });
  }

  async destroy(uploadId: UploadId): Promise<void> {
    await this.port.abortInFlight(uploadId);
    this.port.destroySecrets();
  }
}
