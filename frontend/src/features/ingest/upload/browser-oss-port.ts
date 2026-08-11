import type { UploadId } from './model';
import type { MultipartOssPort, UploadPartInput, UploadedPartFact } from './multipart-controller';

export type OssRequestKind = 'UPLOAD_PART' | 'LIST_PARTS' | 'COMPLETE_MULTIPART';

export interface OssRequestDescriptor {
  readonly kind: OssRequestKind;
  readonly uploadId: UploadId;
  readonly objectId: string;
  readonly partNumber?: string;
  readonly body?: BodyInit;
}

export interface EphemeralSignedOssRequest {
  readonly url: string;
  readonly headers?: Readonly<Record<string, string>>;
  readonly expiresAt: string;
}

export type EphemeralOssRequestFactory = (
  descriptor: OssRequestDescriptor,
  signal: AbortSignal,
) => Promise<EphemeralSignedOssRequest>;

export class OssPortError extends Error {
  constructor(readonly code: 'AUTHORIZATION_EXPIRED' | 'OSS_REQUEST_FAILED' | 'OSS_RESPONSE_INVALID', readonly status: number | null) {
    super(code);
  }
}

function normalizedEtag(raw: string | null): string {
  const value = raw?.trim().replace(/^"|"$/gu, '') ?? '';
  if (!value) throw new OssPortError('OSS_RESPONSE_INVALID', null);
  return value;
}

function assertEphemeralRequest(value: EphemeralSignedOssRequest): URL {
  const url = new URL(value.url);
  if (url.protocol !== 'https:' || !Number.isFinite(Date.parse(value.expiresAt))) throw new OssPortError('OSS_RESPONSE_INVALID', null);
  if (Date.parse(value.expiresAt) <= Date.now()) throw new OssPortError('AUTHORIZATION_EXPIRED', 403);
  return url;
}

function xmlText(parent: Element, name: string): string {
  return parent.getElementsByTagName(name).item(0)?.textContent?.trim() ?? '';
}

/**
 * Browser-only direct OSS transport. Ephemeral URLs remain inside one fetch call and never
 * enter React state, Query keys/cache, persistence, telemetry, errors, or stringified models.
 */
export class BrowserMultipartOssPort implements MultipartOssPort {
  #factory: EphemeralOssRequestFactory | null;
  #inflight = new Map<UploadId, Set<AbortController>>();

  constructor(factory: EphemeralOssRequestFactory) {
    this.#factory = factory;
  }

  async #request(descriptor: OssRequestDescriptor, method: 'GET' | 'POST' | 'PUT', body: BodyInit | undefined, outerSignal: AbortSignal): Promise<Response> {
    if (!this.#factory) throw new OssPortError('AUTHORIZATION_EXPIRED', 403);
    const controller = new AbortController();
    const abort = () => controller.abort();
    outerSignal.addEventListener('abort', abort, { once: true });
    const controllers = this.#inflight.get(descriptor.uploadId) ?? new Set<AbortController>();
    controllers.add(controller);
    this.#inflight.set(descriptor.uploadId, controllers);
    try {
      const ephemeral = await this.#factory(descriptor, controller.signal);
      const url = assertEphemeralRequest(ephemeral);
      const response = await fetch(url, {
        method,
        headers: ephemeral.headers,
        body,
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'omit',
        referrerPolicy: 'no-referrer',
      });
      if (!response.ok) {
        throw new OssPortError(response.status === 401 || response.status === 403 ? 'AUTHORIZATION_EXPIRED' : 'OSS_REQUEST_FAILED', response.status);
      }
      return response;
    } finally {
      outerSignal.removeEventListener('abort', abort);
      controllers.delete(controller);
      if (!controllers.size) this.#inflight.delete(descriptor.uploadId);
    }
  }

  async uploadPart(input: UploadPartInput, signal: AbortSignal): Promise<UploadedPartFact> {
    const response = await this.#request({ kind: 'UPLOAD_PART', uploadId: input.uploadId, objectId: input.objectId, partNumber: input.partNumber, body: input.bytes }, 'PUT', input.bytes, signal);
    return {
      partNumber: input.partNumber,
      sizeBytes: String(input.bytes.size),
      etag: normalizedEtag(response.headers.get('ETag')),
      checksumAlgorithm: response.headers.get('x-oss-hash-crc64ecma') ? 'CRC64_ECMA' : 'OSS_ETAG',
      checksumValue: response.headers.get('x-oss-hash-crc64ecma') ?? normalizedEtag(response.headers.get('ETag')),
      attempt: input.attempt,
    };
  }

  async listParts(uploadId: UploadId, objectId: string, signal: AbortSignal): Promise<readonly UploadedPartFact[]> {
    const response = await this.#request({ kind: 'LIST_PARTS', uploadId, objectId }, 'GET', undefined, signal);
    const document = new DOMParser().parseFromString(await response.text(), 'application/xml');
    if (document.querySelector('parsererror')) throw new OssPortError('OSS_RESPONSE_INVALID', response.status);
    return [...document.getElementsByTagName('Part')].map((part) => ({
      partNumber: xmlText(part, 'PartNumber'),
      sizeBytes: xmlText(part, 'Size'),
      etag: normalizedEtag(xmlText(part, 'ETag')),
      checksumAlgorithm: xmlText(part, 'HashCrc64ecma') ? 'CRC64_ECMA' : 'OSS_ETAG',
      checksumValue: xmlText(part, 'HashCrc64ecma') || normalizedEtag(xmlText(part, 'ETag')),
      attempt: '0',
    }));
  }

  async completeMultipart(uploadId: UploadId, objectId: string, parts: readonly UploadedPartFact[], signal: AbortSignal): Promise<void> {
    const sorted = [...parts].sort((left, right) => Number(BigInt(left.partNumber) - BigInt(right.partNumber)));
    const body = `<CompleteMultipartUpload>${sorted.map((part) => `<Part><PartNumber>${part.partNumber}</PartNumber><ETag>${part.etag}</ETag></Part>`).join('')}</CompleteMultipartUpload>`;
    await this.#request({ kind: 'COMPLETE_MULTIPART', uploadId, objectId, body }, 'POST', body, signal);
  }

  abortInFlight(uploadId: UploadId): Promise<void> {
    for (const controller of this.#inflight.get(uploadId) ?? []) controller.abort();
    this.#inflight.delete(uploadId);
    return Promise.resolve();
  }

  destroySecrets(): void {
    for (const controllers of this.#inflight.values()) for (const controller of controllers) controller.abort();
    this.#inflight.clear();
    this.#factory = null;
  }
}
