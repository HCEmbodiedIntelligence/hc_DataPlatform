import {
  createDomainError,
  type DomainError,
} from "../../../shared/api/domain-error";

export function datasetDataError(
  code: string,
  message: string,
  retryable = false,
): DomainError {
  return createDomainError({
    code: "PRECONDITION_FAILED",
    problemCode: code,
    message,
    fieldErrors: [],
    operationErrors: [{ code, message }],
    blockedReasons: [],
    requestId: null,
    retryable,
    httpStatus: null,
  });
}
