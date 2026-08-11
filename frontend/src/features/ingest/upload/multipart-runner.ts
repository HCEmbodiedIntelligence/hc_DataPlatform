import type { UploadObjectPlanSecret } from './authorization-vault';
import { OssPortError } from './browser-oss-port';
import type { MultipartController, UploadedPartFact } from './multipart-controller';
import type { UploadId } from './model';
import { effectiveUploadConcurrency, isPolicyRetryableStatus, uploadRetryDelayMs, type UploadRuntimePolicy } from './policy-scheduler';

export interface ObjectSha256Port {
  /** Implementations must hash off the React render path, normally in a Web Worker. */
  digest(file: File, signal: AbortSignal): Promise<string>;
}

export interface MultipartObjectProgress {
  readonly uploadObjectId: string;
  readonly completedParts: number;
  readonly totalParts: number;
  readonly completedBytes: string;
  readonly totalBytes: string;
}

export interface CompletedMultipartObject {
  readonly uploadObjectId: string;
  readonly contentSha256: string;
  readonly parts: readonly UploadedPartFact[];
}

function safeInteger(value: string, label: string): number {
  const parsed = BigInt(value);
  if (parsed < 0n || parsed > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error(`${label}_OUT_OF_BROWSER_RANGE`);
  return Number(parsed);
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortError(signal));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(abortError(signal));
    }, { once: true });
  });
}

function abortError(signal: AbortSignal): Error {
  return signal.reason instanceof Error ? signal.reason : new DOMException('Upload aborted', 'AbortError');
}

function assertPartFacts(parts: readonly UploadedPartFact[], totalParts: number): Map<number, UploadedPartFact> {
  const result = new Map<number, UploadedPartFact>();
  for (const part of parts) {
    const number = safeInteger(part.partNumber, 'PART_NUMBER');
    if (number < 1 || number > totalParts || result.has(number)) throw new Error('OSS_PART_RECONCILIATION_FAILED');
    result.set(number, part);
  }
  return result;
}

export class MultipartUploadRunner {
  constructor(
    private readonly controller: MultipartController,
    private readonly sha256: ObjectSha256Port,
    private readonly environmentSafeConcurrency: number,
  ) {}

  async uploadObject(options: {
    readonly uploadId: UploadId;
    readonly plan: UploadObjectPlanSecret;
    readonly file: File;
    readonly policy: UploadRuntimePolicy;
    readonly signal: AbortSignal;
    readonly onProgress?: (progress: MultipartObjectProgress) => void;
  }): Promise<CompletedMultipartObject> {
    const { uploadId, plan, file, policy, signal } = options;
    const expectedSize = safeInteger(plan.sizeBytes, 'OBJECT_SIZE');
    const partSize = safeInteger(plan.partSizeBytes, 'PART_SIZE');
    if (partSize < 1 || file.size !== expectedSize) throw new Error('LOCAL_FILE_PLAN_MISMATCH');
    const totalParts = Math.ceil(file.size / partSize);
    const contentSha256 = this.sha256.digest(file, signal);
    const confirmed = assertPartFacts(await this.controller.listParts(uploadId, plan.uploadObjectId, signal), totalParts);
    let completedBytes = [...confirmed.values()].reduce((total, part) => total + safeInteger(part.sizeBytes, 'PART_SIZE'), 0);
    const publish = () => options.onProgress?.({
      uploadObjectId: plan.uploadObjectId,
      completedParts: confirmed.size,
      totalParts,
      completedBytes: String(completedBytes),
      totalBytes: plan.sizeBytes,
    });
    publish();

    const pending = Array.from({ length: totalParts }, (_, index) => index + 1).filter((partNumber) => !confirmed.has(partNumber));
    const worker = async () => {
      while (pending.length) {
        if (signal.aborted) throw abortError(signal);
        const partNumber = pending.shift();
        if (partNumber === undefined) return;
        const start = (partNumber - 1) * partSize;
        const bytes = file.slice(start, Math.min(file.size, start + partSize));
        let lastError: unknown;
        for (let attempt = 1; attempt <= policy.retry.maxAttemptsPerPart; attempt += 1) {
          try {
            const fact = await this.controller.uploadPart({ uploadId, objectId: plan.uploadObjectId, partNumber: String(partNumber), bytes, attempt: String(attempt) }, signal);
            confirmed.set(partNumber, fact);
            completedBytes += bytes.size;
            publish();
            lastError = undefined;
            break;
          } catch (error) {
            lastError = error;
            const retryable = error instanceof OssPortError && error.status !== null && isPolicyRetryableStatus(policy, error.status);
            if (!retryable || attempt === policy.retry.maxAttemptsPerPart) break;
            await delay(uploadRetryDelayMs(policy, attempt), signal);
          }
        }
        if (lastError) throw lastError instanceof Error ? lastError : new Error('OSS_PART_UPLOAD_FAILED');
      }
    };
    const concurrency = Math.min(pending.length || 1, effectiveUploadConcurrency(policy, this.environmentSafeConcurrency));
    await Promise.all(Array.from({ length: concurrency }, () => worker()));
    const parts = [...confirmed.values()].sort((left, right) => safeInteger(left.partNumber, 'PART_NUMBER') - safeInteger(right.partNumber, 'PART_NUMBER'));
    if (parts.length !== totalParts) throw new Error('OSS_PART_RECONCILIATION_FAILED');
    await this.controller.completeMultipart(uploadId, plan.uploadObjectId, parts, signal);
    const digest = await contentSha256;
    if (!/^[a-f0-9]{64}$/u.test(digest)) throw new Error('OBJECT_SHA256_INVALID');
    return { uploadObjectId: plan.uploadObjectId, contentSha256: digest, parts };
  }
}
