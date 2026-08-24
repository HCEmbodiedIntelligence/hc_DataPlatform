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
import { runtimeOperations } from "./generated/platform-operations";

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
   * Session mode sends the opaque bearer without tenant scope. Public mode
   * sends neither bearer nor tenant headers. Both remain independent from a
   * concurrent project-scope switch.
   */
  scopeMode?: "active" | "session" | "public";
  /**
   * Use one not-yet-installed opaque session for bootstrap or cleanup. This is
   * accepted only in session mode and overrides the shell token.
   */
  bearerToken?: string;
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
  /**
   * Opaque single-use public-auth challenge response. The transport accepts it only for public
   * operations, sends it as a header, and never places it in a URL, cache key, or error.
   */
  authChallengeResponse?: string;
  signal?: AbortSignal;
  /**
   * Short-lived authorization responses must never enter any HTTP cache.
   * Callers handling STS/presign/renewal payloads set this to 'no-store'.
   */
  cache?: RequestCache;
};

export function isSafeBearerToken(value: string): boolean {
  return /^[!-~]{1,4096}$/u.test(value);
}

interface ErrorEnvelopeWire {
  error?: {
    code?: unknown;
    message?: unknown;
    field_errors?: unknown;
    operation_errors?: unknown;
    blocked_reasons?: unknown;
    request_id?: unknown;
    retryable?: unknown;
    retry_after_seconds?: unknown;
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
const maximumRetryAfterSeconds = 86_400;

function resolveAcceptLanguage(): string {
  const candidate = globalThis.navigator?.language;
  return typeof candidate === "string" && candidate.trim()
    ? candidate
    : defaultAcceptLanguage;
}

export function domainErrorFromResponse(
  status: number,
  raw: unknown,
  headers?: Headers,
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
      "retry_after_seconds",
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
    retryAfterSeconds: retryAfterSeconds(status, headers, metadata),
    httpStatus: status,
  });
}

function retryAfterSeconds(
  status: number,
  headers: Headers | undefined,
  metadata: ErrorPayloadWire | null,
): number | null {
  if (status !== 429 && status !== 503) return null;
  const header = headers?.get("Retry-After");
  if (header !== null && header !== undefined) {
    if (!/^[0-9]+$/u.test(header)) return null;
    const canonical = header.replace(/^0+/u, "");
    if (!canonical) return null;
    if (
      canonical.length > 5 ||
      (canonical.length === 5 && canonical > String(maximumRetryAfterSeconds))
    ) {
      return maximumRetryAfterSeconds;
    }
    const parsed = Number(canonical);
    return parsed >= 1 ? parsed : null;
  }
  const bodyValue = metadata?.retry_after_seconds;
  if (
    typeof bodyValue !== "number" ||
    !Number.isSafeInteger(bodyValue) ||
    bodyValue < 1
  ) {
    return null;
  }
  return Math.min(bodyValue, maximumRetryAfterSeconds);
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

const runtimeParameterToken = /^\{[A-Za-z_][A-Za-z0-9_]*\}$/u;
const runtimeParameterPart = /(\{[A-Za-z_][A-Za-z0-9_]*\})/u;
const regularExpressionSpecialCharacter = /[.*+?^${}()|[\]\\]/gu;

function runtimePathSegmentMatches(
  templateSegment: string,
  requestSegment: string,
): boolean {
  const expression = templateSegment
    .split(runtimeParameterPart)
    .map((part) =>
      runtimeParameterToken.test(part)
        ? "[^/]+"
        : part.replace(regularExpressionSpecialCharacter, "\\$&"),
    )
    .join("");
  return new RegExp(`^${expression}$`, "u").test(requestSegment);
}

function runtimeOperationMatches(
  template: string,
  requestPath: string,
): boolean {
  const expected = template.split("/");
  const actual = requestPath.split("?")[0]?.split("/") ?? [];
  return (
    expected.length === actual.length &&
    expected.every((segment, index) =>
      runtimePathSegmentMatches(segment, actual[index] ?? ""),
    )
  );
}

function assertRuntimeOperation(method: RequestMethod, path: string): void {
  if (
    runtimeOperations.some(
      (operation) =>
        operation.method === method &&
        runtimeOperationMatches(operation.path, path),
    )
  ) {
    return;
  }
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    problemCode: "RUNTIME_OPERATION_UNDECLARED",
    message: "客户端请求不在当前正式 API 合同中。",
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [
      {
        code: "RUNTIME_OPERATION_UNDECLARED",
        message: `${method} ${path} is absent from the production runtime OpenAPI.`,
      },
    ],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

export async function request<T>(opts: RequestOptions): Promise<T> {
  const config = getRuntimeConfig();
  const shell = getShellState();
  const sessionScoped = opts.scopeMode === "session";
  const publicScoped = opts.scopeMode === "public";
  const activeScoped = !sessionScoped && !publicScoped;
  if (opts.bearerToken !== undefined) {
    if (!sessionScoped) {
      throw new TypeError("Explicit bearer tokens require session scope mode");
    }
    if (!isSafeBearerToken(opts.bearerToken)) {
      throw new TypeError(
        "Explicit bearer tokens must contain visible ASCII only",
      );
    }
  }
  if (!activeScoped && opts.scope !== undefined) {
    throw new TypeError("Unscoped requests cannot bind a project scope");
  }
  if (opts.authChallengeResponse !== undefined) {
    if (!publicScoped) {
      throw new TypeError(
        "Authentication challenge responses require public scope mode",
      );
    }
    if (!/^[!-~]{1,2048}$/u.test(opts.authChallengeResponse)) {
      throw new TypeError(
        "Authentication challenge responses must be visible ASCII up to 2048 chars",
      );
    }
  }
  if (
    activeScoped &&
    shell.scopeChanging &&
    opts.method !== "GET" &&
    opts.method !== "HEAD"
  ) {
    throw scopeTransitionError(
      "SCOPE_SWITCH_IN_PROGRESS",
      "作用域切换期间禁止提交写请求",
    );
  }
  const requestScope = activeScoped ? (opts.scope ?? shell.scope) : null;
  const requestScopeKey =
    requestScope === null ? shell.scopeKey : makeScopeKey(requestScope);
  if (
    activeScoped &&
    opts.scope !== undefined &&
    shell.scopeKey !== requestScopeKey
  ) {
    throw scopeTransitionError(
      "SCOPE_CHANGED",
      "Request scope no longer matches the active scope",
    );
  }
  assertRuntimeOperation(opts.method, opts.path);
  const headers = new Headers({
    Accept: "application/json",
    "Accept-Language": resolveAcceptLanguage(),
    "X-Client-Version": config.buildVersion,
  });
  const bearerToken = opts.bearerToken ?? shell.sessionToken;
  if (!publicScoped && bearerToken)
    headers.set("Authorization", `Bearer ${bearerToken}`);
  if (requestScope?.organizationId)
    headers.set("X-Organization-Id", requestScope.organizationId);
  if (requestScope?.projectId)
    headers.set("X-Project-Id", requestScope.projectId);
  if (requestScope?.regionCode)
    headers.set("X-Region-Code", requestScope.regionCode);
  if (opts.idempotencyKey) headers.set("Idempotency-Key", opts.idempotencyKey);
  if (opts.ifMatch) headers.set("If-Match", opts.ifMatch);
  if (opts.authChallengeResponse !== undefined) {
    headers.set("X-Auth-Challenge-Response", opts.authChallengeResponse);
  }
  if (opts.body !== undefined) headers.set("Content-Type", "application/json");

  const { controller, release } = createManagedAbortController(opts.signal, {
    cancelOnScopeChange: activeScoped,
  });
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
    if (activeScoped && getShellState().scopeKey !== requestScopeKey) {
      throw scopeTransitionError("SCOPE_CHANGED", "请求所属作用域已失效");
    }
    const raw = await readJson(response);
    if (!response.ok)
      throw domainErrorFromResponse(response.status, raw, response.headers);
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
