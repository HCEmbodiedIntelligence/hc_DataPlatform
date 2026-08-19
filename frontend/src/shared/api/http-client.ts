import { getRuntimeConfig } from "../config/runtime";
import { makeScopeKey, type Scope } from "../../entities/scope";
import { camelToSnake } from "../lib/case-convert";
import { getShellState } from "../scope/shell-store";
import {
  createDomainError,
  domainErrorCodeForStatus,
  isDomainError,
  type DomainBlockedReason,
  type DomainError,
  type DomainFieldError,
  type DomainOperationError,
} from "./domain-error";
import { createManagedAbortController } from "./transport-lifecycle";

export type RequestMethod =
  | "GET"
  | "POST"
  | "PUT"
  | "PATCH"
  | "DELETE"
  | "HEAD";

/** Scalars accepted on the HTTP wire query string. buildQuery rejects anything else. */
export type QueryScalar = string | number | boolean;
export type QueryValue =
  | QueryScalar
  | readonly QueryScalar[]
  | null
  | undefined;

export type RequestOptions = {
  method: RequestMethod;
  path: string;
  /**
   * Bind a scoped request to one immutable scope snapshot. When supplied,
   * both request headers and the post-response scope guard use this value.
   */
  scope?: Scope;
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
    code?: unknown;
    message?: unknown;
    field_errors?: unknown;
    operation_errors?: unknown;
    blocked_reasons?: unknown;
    request_id?: unknown;
    retryable?: unknown;
  };
}

type ErrorPayloadWire = Record<string, unknown>;

function joinUrl(base: string, requestPath: string): string {
  if (!base.trim()) throw new Error("VITE_API_BASE_URL must not be empty");
  const normalizedBase = base.endsWith("/") ? base.slice(0, -1) : base;
  const normalizedPath = requestPath.startsWith("/")
    ? requestPath
    : `/${requestPath}`;
  return `${normalizedBase}${normalizedPath}`;
}

function buildQuery(query: RequestOptions["query"]): string {
  if (query === undefined) return "";
  const params = new URLSearchParams();
  for (const [camelKey, raw] of Object.entries(query)) {
    const values: readonly unknown[] = Array.isArray(raw) ? raw : [raw];
    for (const value of values) {
      if (value === null || value === undefined) continue;
      if (
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
      ) {
        params.append(camelToSnake(camelKey), String(value));
      } else {
        throw new TypeError(
          `Query parameter ${camelKey} must be a scalar or scalar array`,
        );
      }
    }
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

function asIssueArray<T>(
  value: unknown,
  mapper: (entry: Record<string, unknown>) => T,
): T[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry))
      return [];
    return [mapper(entry as Record<string, unknown>)];
  });
}

function nonEmptyText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized ? normalized : null;
}

function text(value: unknown, fallback = ""): string {
  return nonEmptyText(value) ?? fallback;
}

function asRecord(value: unknown): ErrorPayloadWire | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as ErrorPayloadWire)
    : null;
}

const defaultAcceptLanguage = "zh-CN";

function resolveAcceptLanguage(): string {
  const candidate = globalThis.navigator?.language;
  return typeof candidate === "string" && candidate.trim()
    ? candidate
    : defaultAcceptLanguage;
}

export function domainErrorFromResponse(
  status: number,
  raw: unknown,
): DomainError & Error {
  const payload = asRecord(raw);
  const envelope = asRecord((payload as ErrorEnvelopeWire | null)?.error);
  const hasProblemDetailsField =
    payload !== null &&
    [
      "type",
      "title",
      "status",
      "detail",
      "code",
      "request_id",
      "retryable",
      "details",
    ].some((key) => Object.hasOwn(payload, key));
  const isLegacyEnvelope = envelope !== null && !hasProblemDetailsField;
  const metadata = isLegacyEnvelope ? envelope : payload;
  const structuredDetails = isLegacyEnvelope
    ? envelope
    : asRecord(payload?.details);
  const fieldErrors = asIssueArray<DomainFieldError>(
    structuredDetails?.field_errors,
    (entry) => ({
      path: text(entry.path, "/"),
      code: text(entry.code, "INVALID"),
      message: text(entry.message, "字段值无效"),
    }),
  );
  const operationErrors = asIssueArray<DomainOperationError>(
    structuredDetails?.operation_errors,
    (entry) => {
      const operationId = text(entry.operation_id);
      return {
        code: text(entry.code, "OPERATION_FAILED"),
        message: text(entry.message, "操作失败"),
        ...(operationId ? { operationId } : {}),
      };
    },
  );
  const blockedReasons = asIssueArray<DomainBlockedReason>(
    structuredDetails?.blocked_reasons,
    (entry) => ({
      code: text(entry.code, "BLOCKED"),
      message: text(entry.message, "当前资源不可执行此操作"),
    }),
  );
  const message = isLegacyEnvelope
    ? nonEmptyText(metadata?.message)
    : (nonEmptyText(payload?.detail) ?? nonEmptyText(payload?.title));
  return createDomainError({
    code: domainErrorCodeForStatus(status),
    problemCode: nonEmptyText(metadata?.code),
    message: message ?? "请求未成功完成",
    fieldErrors,
    operationErrors,
    blockedReasons,
    requestId: nonEmptyText(metadata?.request_id),
    retryable: metadata?.retryable === true,
    httpStatus: status,
  });
}

async function readJson(response: Response): Promise<unknown> {
  let textBody: string;
  try {
    textBody = await response.text();
  } catch {
    return undefined;
  }
  if (!textBody) return undefined;
  try {
    return JSON.parse(textBody) as unknown;
  } catch {
    return undefined;
  }
}

function scopeTransitionError(
  code: string,
  message: string,
): DomainError & Error {
  return createDomainError({
    code: "PRECONDITION_FAILED",
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
  if (shell.scopeChanging && opts.method !== "GET" && opts.method !== "HEAD") {
    throw scopeTransitionError(
      "SCOPE_SWITCH_IN_PROGRESS",
      "作用域切换期间禁止提交写请求",
    );
  }
  const requestScope = opts.scope ?? shell.scope;
  const requestScopeKey =
    requestScope === null ? shell.scopeKey : makeScopeKey(requestScope);
  if (opts.scope !== undefined && shell.scopeKey !== requestScopeKey) {
    throw scopeTransitionError(
      "SCOPE_CHANGED",
      "Request scope no longer matches the active scope",
    );
  }
  const headers = new Headers({
    Accept: "application/json",
    "Accept-Language": resolveAcceptLanguage(),
    "X-Client-Version": config.buildVersion,
  });
  if (shell.sessionToken)
    headers.set("Authorization", `Bearer ${shell.sessionToken}`);
  if (requestScope?.organizationId)
    headers.set("X-Organization-Id", requestScope.organizationId);
  if (requestScope?.projectId)
    headers.set("X-Project-Id", requestScope.projectId);
  if (requestScope?.regionCode)
    headers.set("X-Region-Code", requestScope.regionCode);
  if (opts.idempotencyKey) headers.set("Idempotency-Key", opts.idempotencyKey);
  if (opts.ifMatch) headers.set("If-Match", opts.ifMatch);
  if (opts.body !== undefined) headers.set("Content-Type", "application/json");

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
      throw scopeTransitionError("SCOPE_CHANGED", "请求所属作用域已失效");
    }
    const raw = await readJson(response);
    if (!response.ok) throw domainErrorFromResponse(response.status, raw);
    return raw as T;
  } catch (error) {
    if (isDomainError(error)) throw error;
    throw createDomainError({
      code: "NETWORK_ERROR",
      message: controller.signal.aborted ? "请求已取消" : "网络连接失败",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: !controller.signal.aborted && opts.method === "GET",
      httpStatus: null,
    });
  } finally {
    release();
  }
}
