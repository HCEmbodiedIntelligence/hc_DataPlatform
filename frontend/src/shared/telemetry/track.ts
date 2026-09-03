import { getRuntimeConfig } from '../config/runtime';
import { getShellState } from '../scope/shell-store';

export interface TrackEvent {
  pageId: string;
  routePattern: string;
  operation: string;
  duration: number;
  outcome: string;
  httpStatus: number | null;
  errorCode: string | null;
  requestId: string | null;
  contractVersion: string;
  buildVersion?: string;
  scopeHash?: string;
  metadata?: Readonly<Record<string, unknown>>;
}

export type TelemetryRecord = Omit<TrackEvent, 'metadata'> & {
  metadata?: Readonly<Record<string, unknown>>;
};

type TelemetrySink = (record: TelemetryRecord) => void;
let sink: TelemetrySink = (record) => console.info('frontend_telemetry', record);

const sensitiveKey =
  /token|secret|access.?key|authorization|cookie|session|sts|signature|signed.?url|bucket|oss.?key|object.?key|manifest|credential/iu;

function looksLikeSignedUrl(value: string): boolean {
  if (!/^https?:\/\//iu.test(value)) return false;
  return /[?&](?:x-amz-|signature=|token=|expires=)/iu.test(value);
}

function looksSensitiveString(value: string): boolean {
  const trimmed = value.trim();
  return (
    looksLikeSignedUrl(trimmed) ||
    /^(?:bearer\s+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.)/u.test(trimmed) ||
    /^(?:oss|s3|cos|gs):\/\//iu.test(trimmed) ||
    /^https?:\/\/[^/]*(?:aliyuncs\.com|amazonaws\.com|myqcloud\.com)\//iu.test(trimmed) ||
    /^[^/?#\s]+(?:\/[^/?#\s]+){2,}$/u.test(trimmed) ||
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  );
}

function redact(value: unknown, seen = new WeakSet<object>()): unknown {
  if (typeof value === 'string') return looksSensitiveString(value) ? '[REDACTED]' : value;
  if (Array.isArray(value)) return value.map((entry) => redact(entry, seen));
  if (typeof value !== 'object' || value === null) return value;
  if (seen.has(value)) return '[REDACTED_CYCLE]';
  seen.add(value);
  const output: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(value)) {
    output[key] = sensitiveKey.test(key) ? '[REDACTED]' : redact(entry, seen);
  }
  return output;
}

function hashScope(scopeKey: string): string {
  let hash = 0x811c9dc5;
  for (const char of scopeKey) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193);
  }
  return `scope_${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

export function configureTelemetrySink(nextSink: TelemetrySink): void {
  sink = nextSink;
}

export function track(event: TrackEvent): void {
  const runtime = getRuntimeConfig();
  const safeMetadata = event.metadata ? (redact(event.metadata) as Record<string, unknown>) : undefined;
  const record: TelemetryRecord = {
    pageId: looksSensitiveString(event.pageId) ? '[REDACTED]' : event.pageId,
    routePattern: looksSensitiveString(event.routePattern) ? '[REDACTED]' : event.routePattern,
    scopeHash:
      event.scopeHash === undefined
        ? hashScope(getShellState().scopeKey)
        : looksSensitiveString(event.scopeHash)
          ? '[REDACTED]'
          : event.scopeHash,
    operation: looksSensitiveString(event.operation) ? '[REDACTED]' : event.operation,
    duration: event.duration,
    outcome: looksSensitiveString(event.outcome) ? '[REDACTED]' : event.outcome,
    httpStatus: event.httpStatus,
    errorCode:
      event.errorCode !== null && looksSensitiveString(event.errorCode)
        ? '[REDACTED]'
        : event.errorCode,
    requestId:
      event.requestId !== null && looksSensitiveString(event.requestId)
        ? '[REDACTED]'
        : event.requestId,
    contractVersion: looksSensitiveString(event.contractVersion)
      ? '[REDACTED]'
      : event.contractVersion,
    buildVersion: looksSensitiveString(event.buildVersion ?? runtime.buildVersion)
      ? '[REDACTED]'
      : (event.buildVersion ?? runtime.buildVersion),
    ...(safeMetadata ? { metadata: safeMetadata } : {}),
  };
  sink(record);
}
