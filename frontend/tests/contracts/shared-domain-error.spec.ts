import { describe, expect, it } from 'vitest';
import { domainErrorFromResponse } from '../../src/shared/api/http-client';
import { isDomainError, type DomainErrorCode } from '../../src/shared/api/domain-error';

const mappings: readonly [number, DomainErrorCode][] = [
  [400, 'VALIDATION_ERROR'],
  [422, 'VALIDATION_ERROR'],
  [401, 'UNAUTHENTICATED'],
  [403, 'FORBIDDEN'],
  [404, 'NOT_FOUND'],
  [409, 'VERSION_CONFLICT'],
  [412, 'PRECONDITION_FAILED'],
  [410, 'GONE'],
  [429, 'RATE_LIMITED'],
  [500, 'SERVER_ERROR'],
  [503, 'SERVER_ERROR'],
];

describe('domain error mapping', () => {
  it.each(mappings)('maps HTTP %s to %s', (status, code) => {
    const error = domainErrorFromResponse(status, {
      error: {
        code: 'WIRE_CODE',
        message: '安全错误',
        field_errors: [{ path: '/name', code: 'INVALID', message: '字段错误' }],
        operation_errors: [{ code: 'FAILED', message: '操作错误' }],
        blocked_reasons: [{ code: 'BLOCKED', message: '资源阻断' }],
        request_id: 'req_fx_error',
        retryable: false,
      },
    });
    expect(isDomainError(error)).toBe(true);
    expect(error).toMatchObject({ code, httpStatus: status, requestId: 'req_fx_error' });
    expect(error.fieldErrors[0]?.path).toBe('/name');
    expect(error.operationErrors[0]?.code).toBe('FAILED');
    expect(error.blockedReasons[0]?.code).toBe('BLOCKED');
  });
});
