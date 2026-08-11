import { safeReturnTo } from '../../shared/routing/route-registry';

export type IngestUrl = string & { readonly __brand: 'IngestUrl' };

export type IngestQueryValue = string | number | boolean | readonly string[] | null | undefined;
export type IngestQueryInput = Readonly<Record<string, IngestQueryValue>>;

const SENSITIVE_QUERY_KEY = /(?:access|secret|token|signature|signed|bucket|objectkey|multipart|credential|authorization)/i;

function assertStableId(value: string, name: string): void {
  if (!value || value === 'latest' || value === 'current' || /[/\\?#]/.test(value)) {
    throw new Error(`${name} must be a stable immutable ID`);
  }
}

function appendQuery(path: string, query: IngestQueryInput = {}): IngestUrl {
  const search = new URLSearchParams();
  for (const key of Object.keys(query).sort()) {
    if (SENSITIVE_QUERY_KEY.test(key)) continue;
    const value = query[key];
    if (value === undefined || value === null || value === '' || value === false) continue;
    const items: readonly (string | number | boolean)[] = typeof value === 'object' ? [...value].sort() : [value];
    for (const item of items) {
      search.append(key, String(item));
    }
  }
  return `${path}${search.size ? `?${search}` : ''}` as IngestUrl;
}

export function isSafeIngestReturnTo(value: string | null | undefined): value is IngestUrl {
  return safeReturnTo(value) !== null;
}

export const routes = {
  sources: {
    path: '/ingest/sources',
    build(query: IngestQueryInput = {}): IngestUrl {
      return appendQuery('/ingest/sources', query);
    },
  },
  uploadJobs: {
    path: '/ingest/uploads',
    build(query: IngestQueryInput = {}): IngestUrl {
      return appendQuery('/ingest/uploads', query);
    },
  },
  uploads: {
    path: '/ingest/uploads/:uploadId',
    build({ uploadId }: { readonly uploadId: string }, query: IngestQueryInput = {}): IngestUrl {
      assertStableId(uploadId, 'uploadId');
      return appendQuery(`/ingest/uploads/${encodeURIComponent(uploadId)}`, query);
    },
  },
} as const;

export function buildP02CreateSourceUrl(returnTo?: string): IngestUrl {
  return routes.sources.build({ intent: 'create', returnTo: safeReturnTo(returnTo) ?? undefined });
}

export function buildP03UploadsUrl(query: IngestQueryInput = {}): IngestUrl {
  return routes.uploadJobs.build(query);
}

export function buildP04UploadDetailUrl(
  input: { readonly uploadId: string; readonly tab?: string; readonly objectId?: string; readonly returnTo?: string },
): IngestUrl {
  return routes.uploads.build(
    { uploadId: input.uploadId },
    {
      tab: input.tab,
      objectId: input.objectId,
      returnTo: safeReturnTo(input.returnTo) ?? undefined,
    },
  );
}
