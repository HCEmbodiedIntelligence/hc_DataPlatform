import type { ZodType } from 'zod';
import { createDomainError } from './domain-error';

export interface WireParseContext {
  endpoint: string;
  schemaVersion?: string;
  requestId?: string | null;
}

function escapeJsonPointer(value: string): string {
  return value.replaceAll('~', '~0').replaceAll('/', '~1');
}

function issuePointer(path: PropertyKey[]): string {
  if (path.length === 0) return '/';
  return `/${path
    .map((part) => escapeJsonPointer(typeof part === 'symbol' ? '<symbol>' : String(part)))
    .join('/')}`;
}

function safeEndpoint(endpoint: string): string {
  try {
    if (/^https?:\/\//iu.test(endpoint)) return new URL(endpoint).pathname;
  } catch {
    return '<invalid-endpoint>';
  }
  return endpoint.split(/[?#]/u, 1)[0] ?? '<unknown-endpoint>';
}

export function parseWire<T>(schema: ZodType<T>, raw: unknown, ctx: WireParseContext): T {
  const result = schema.safeParse(raw);
  if (result.success) return result.data;
  const pointers = [...new Set(result.error.issues.map((issue) => issuePointer(issue.path)))];
  console.error('wire_contract_mismatch', {
    endpoint: safeEndpoint(ctx.endpoint),
    schemaVersion: ctx.schemaVersion ?? 'unknown',
    requestId: ctx.requestId ?? null,
    pointers,
  });
  throw createDomainError({
    code: 'CONTRACT_MISMATCH',
    message: '服务端响应与当前合同不匹配',
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: ctx.requestId ?? null,
    retryable: false,
    httpStatus: null,
  });
}
