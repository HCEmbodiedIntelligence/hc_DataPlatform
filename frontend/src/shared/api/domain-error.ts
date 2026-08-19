export type DomainErrorCode =
  | "VALIDATION_ERROR"
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "VERSION_CONFLICT"
  | "PRECONDITION_FAILED"
  | "GONE"
  | "RATE_LIMITED"
  | "SERVER_ERROR"
  | "NETWORK_ERROR"
  | "CONTRACT_MISMATCH";

export interface DomainFieldError {
  path: string;
  code: string;
  message: string;
}

export interface DomainOperationError {
  code: string;
  message: string;
  operationId?: string;
}

export interface DomainBlockedReason {
  code: string;
  message: string;
}

export interface DomainError {
  code: DomainErrorCode;
  problemCode: string | null;
  message: string;
  fieldErrors: readonly DomainFieldError[];
  operationErrors: readonly DomainOperationError[];
  blockedReasons: readonly DomainBlockedReason[];
  requestId: string | null;
  retryable: boolean;
  httpStatus: number | null;
}

export type DomainErrorInput = Omit<DomainError, "problemCode"> & {
  problemCode?: string | null;
};

const domainErrorCodes: ReadonlySet<string> = new Set<DomainErrorCode>([
  "VALIDATION_ERROR",
  "UNAUTHENTICATED",
  "FORBIDDEN",
  "NOT_FOUND",
  "VERSION_CONFLICT",
  "PRECONDITION_FAILED",
  "GONE",
  "RATE_LIMITED",
  "SERVER_ERROR",
  "NETWORK_ERROR",
  "CONTRACT_MISMATCH",
]);

class DomainErrorImpl extends Error implements DomainError {
  readonly code: DomainErrorCode;
  readonly problemCode: string | null;
  readonly fieldErrors: readonly DomainFieldError[];
  readonly operationErrors: readonly DomainOperationError[];
  readonly blockedReasons: readonly DomainBlockedReason[];
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly httpStatus: number | null;

  constructor(error: DomainErrorInput) {
    super(error.message);
    this.name = "DomainError";
    this.code = error.code;
    this.problemCode = error.problemCode ?? null;
    this.fieldErrors = error.fieldErrors;
    this.operationErrors = error.operationErrors;
    this.blockedReasons = error.blockedReasons;
    this.requestId = error.requestId;
    this.retryable = error.retryable;
    this.httpStatus = error.httpStatus;
  }
}

export function createDomainError(
  error: DomainErrorInput,
): DomainError & Error {
  return new DomainErrorImpl(error);
}

export function domainErrorCodeForStatus(status: number): DomainErrorCode {
  if (status === 400 || status === 422) return "VALIDATION_ERROR";
  if (status === 401) return "UNAUTHENTICATED";
  if (status === 403) return "FORBIDDEN";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "VERSION_CONFLICT";
  if (status === 412) return "PRECONDITION_FAILED";
  if (status === 410) return "GONE";
  if (status === 429) return "RATE_LIMITED";
  return "SERVER_ERROR";
}

export function isDomainError(error: unknown): error is DomainError & Error {
  if (typeof error !== "object" || error === null) return false;
  const candidate = error as Record<string, unknown>;
  return (
    typeof candidate.code === "string" &&
    domainErrorCodes.has(candidate.code) &&
    (typeof candidate.problemCode === "string" ||
      candidate.problemCode === null) &&
    typeof candidate.message === "string" &&
    Array.isArray(candidate.fieldErrors) &&
    Array.isArray(candidate.operationErrors) &&
    Array.isArray(candidate.blockedReasons) &&
    (typeof candidate.requestId === "string" || candidate.requestId === null) &&
    typeof candidate.retryable === "boolean" &&
    (typeof candidate.httpStatus === "number" || candidate.httpStatus === null)
  );
}
