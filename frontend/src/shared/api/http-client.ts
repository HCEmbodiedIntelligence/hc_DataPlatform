import { getRuntimeConfig } from '../config/runtime';
import { camelToSnake } from '../lib/case-convert';
import { getShellState } from '../scope/shell-store';
import {
  createDomainError,
  domainErrorCodeForStatus,
  isDomainError,
  type DomainBlockedReason,
  type DomainError,
  type DomainFieldError,
  type DomainOperationError,
} from './domain-error';
import { createManagedAbortController } from './transport-lifecycle';

export type RequestMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD';

/** Scalars accepted on the HTTP wire query string. buildQuery rejects anything else. */
export type QueryScalar = string | number | boolean;
export type QueryValue = QueryScalar | readonly QueryScalar[] | null | undefined;

export type RequestOptions = {
  method: RequestMethod;
  path: string;
  /**
   * Callers may pass a wider record; buildQuery throws TypeError on any
   * non-scalar value. Prefer typing your own query records as
   * Record<string, QueryValue> so violations surface at compile time.
   */
  query?: Readonly<Record<string, unknown>>;
  body?: unknown;
  idempotencyKey?: string;
  ifMatch?: string;
  signal?: AbortSignal;
  /**
   * Short-lived authorization responses must never enter any HTTP cache.
   * Callers handling STS/presign/renewal payloads set this to 'no-store'.
   */
  cache?: RequestCache;
};

interface ErrorEnvelopeWire {
  error?: {
    message?: unknown;
    field_errors?: unknown;
    operation_errors?: unknown;
    blocked_reasons?: unknown;
    request_id?: unknown;
    retryable?: unknown;
  };
}

function joinUrl(base: string, requestPath: string): string {
  if (!base.trim()) throw new Error('VITE_API_BASE_URL must not be empty');
  const normalizedBase = base.endsWith('/') ? base.slice(0, -1) : base;
  const normalizedPath = requestPath.startsWith('/') ? requestPath : `/${requestPath}`;
  return `${normalizedBase}${normalizedPath}`;
}

function buildQuery(query: RequestOptions['query']): string {
  if (query === undefined) return '';
  const params = new URLSearchParams();
  for (const [camelKey, raw] of Object.entries(query)) {
    const values: readonly unknown[] = Array.isArray(raw) ? raw : [raw];
    for (const value of values) {
      if (value === null || value === undefined) continue;
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        params.append(camelToSnake(camelKey), String(value));
      } else {
        throw new TypeError(`Query parameter ${camelKey} must be a scalar or scalar array`);
      }
    }
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}

function asIssueArray<T>(value: unknown, mapper: (entry: Record<string, unknown>) => T): T[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (typeof entry !== 'object' || entry === null) return [];
    return [mapper(entry as Record<string, unknown>)];
  });
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

const defaultAcceptLanguage = 'zh-CN';

function resolveAcceptLanguage(): string {
  const candidate = globalThis.navigator?.language;
  return typeof candidate === 'string' && candidate.trim() ? candidate : defaultAcceptLanguage;
}

export function domainErrorFromResponse(status: number, raw: unknown): DomainError & Error {
  const envelope =
    typeof raw === 'object' && raw !== null ? (raw as ErrorEnvelopeWire).error : undefined;
  const fieldErrors = asIssueArray<DomainFieldError>(envelope?.field_errors, (entry) => ({
    path: text(entry.path, '/'),
    code: text(entry.code, 'INVALID'),
    message: text(entry.message, '字段值无效'),
  }));
  const operationErrors = asIssueArray<DomainOperationError>(
    envelope?.operation_errors,
    (entry) => {
      const operationId = text(entry.operation_id);
      return {
        code: text(entry.code, 'OPERATION_FAILED'),
        message: text(entry.message, '操作失败'),
        ...(operationId ? { operationId } : {}),
      };
    },
  );
  const blockedReasons = asIssueArray<DomainBlockedReason>(envelope?.blocked_reasons, (entry) => ({
    code: text(entry.code, 'BLOCKED'),
    message: text(entry.message, '当前资源不可执行此操作'),
  }));
  return createDomainError({
    code: domainErrorCodeForStatus(status),
    message: text(envelope?.message, '请求未成功完成'),
    fieldErrors,
    operationErrors,
    blockedReasons,
    requestId: typeof envelope?.request_id === 'string' ? envelope.request_id : null,
    retryable: envelope?.retryable === true,
    httpStatus: status,
  });
}

async function readJson(response: Response): Promise<unknown> {
  const textBody = await response.text();
  if (!textBody) return undefined;
  try {
    return JSON.parse(textBody) as unknown;
  } catch {
    return undefined;
  }
}

function scopeTransitionError(code: string, message: string): DomainError & Error {
  return createDomainError({
    code: 'PRECONDITION_FAILED',
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [{ code, message }],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

export async function request<T>(opts: RequestOptions): Promise<T> {
  const config = getRuntimeConfig();
  const shell = getShellState();
  if (shell.scopeChanging && opts.method !== 'GET' && opts.method !== 'HEAD') {
    throw scopeTransitionError('SCOPE_SWITCH_IN_PROGRESS', '作用域切换期间禁止提交写请求');
  }
  const requestScopeKey = shell.scopeKey;
  const headers = new Headers({
    Accept: 'application/json',
    'Accept-Language': resolveAcceptLanguage(),
    'X-Client-Version': config.buildVersion,
  });
  if (shell.sessionToken) headers.set('Authorization', `Bearer ${shell.sessionToken}`);
  if (shell.scope?.organizationId) headers.set('X-Organization-Id', shell.scope.organizationId);
  if (shell.scope?.projectId) headers.set('X-Project-Id', shell.scope.projectId);
  if (shell.scope?.regionCode) headers.set('X-Region-Code', shell.scope.regionCode);
  if (opts.idempotencyKey) headers.set('Idempotency-Key', opts.idempotencyKey);
  if (opts.ifMatch) headers.set('If-Match', opts.ifMatch);
  if (opts.body !== undefined) headers.set('Content-Type', 'application/json');

  const { controller, release } = createManagedAbortController(opts.signal);
  try {
    const response = await globalThis.fetch(
      `${joinUrl(config.apiBaseUrl, opts.path)}${buildQuery(opts.query)}`,
      {
        method: opts.method,
        headers,
        signal: controller.signal,
        ...(opts.cache === undefined ? {} : { cache: opts.cache }),
        ...(opts.body === undefined ? {} : { body: JSON.stringify(opts.body) }),
      },
    );
    if (getShellState().scopeKey !== requestScopeKey) {
      throw scopeTransitionError('SCOPE_CHANGED', '请求所属作用域已失效');
    }
    const raw = await readJson(response);
    if (!response.ok) throw domainErrorFromResponse(response.status, raw);
    return raw as T;
  } catch (error) {
    if (isDomainError(error)) throw error;
    throw createDomainError({
      code: 'NETWORK_ERROR',
      message: controller.signal.aborted ? '请求已取消' : '网络连接失败',
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: !controller.signal.aborted && opts.method === 'GET',
      httpStatus: null,
    });
  } finally {
    release();
  }
}
