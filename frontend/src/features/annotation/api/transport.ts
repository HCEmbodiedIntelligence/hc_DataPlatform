import { request } from '../../../shared/api/http-client';

export interface AnnotationRequest {
  readonly method: 'GET' | 'POST' | 'PUT';
  readonly path: string;
  readonly query?: Readonly<Record<string, string | number | readonly string[] | undefined>>;
  readonly headers?: Readonly<Record<string, string>>;
  readonly body?: unknown;
  readonly signal?: AbortSignal;
}

export interface AnnotationTransport {
  request(request: AnnotationRequest): Promise<unknown>;
}

const sharedHttpTransport: AnnotationTransport = {
  request: (input) => request<unknown>({
    method: input.method,
    path: input.path,
    ...(input.query ? { query: input.query } : {}),
    ...(input.body !== undefined ? { body: input.body } : {}),
    ...(input.headers?.['Idempotency-Key'] ? { idempotencyKey: input.headers['Idempotency-Key'] } : {}),
    ...(input.headers?.['If-Match'] ? { ifMatch: input.headers['If-Match'] } : {}),
    ...(input.signal ? { signal: input.signal } : {}),
  }),
};

let configuredTransport: AnnotationTransport | null = sharedHttpTransport;

export function configureAnnotationTransport(transport: AnnotationTransport): () => void {
  configuredTransport = transport;
  return () => { if (configuredTransport === transport) configuredTransport = sharedHttpTransport; };
}

export function getAnnotationTransport(): AnnotationTransport {
  if (!configuredTransport) throw Object.assign(new Error('Annotation API transport is unavailable'), { code: 'FEATURE_UNAVAILABLE' });
  return configuredTransport;
}

export function createIdempotencyKey(): string {
  if (typeof crypto === 'undefined' || typeof crypto.randomUUID !== 'function') {
    throw Object.assign(new Error('Secure idempotency key generation is unavailable'), { code: 'FEATURE_UNAVAILABLE' });
  }
  return crypto.randomUUID();
}
