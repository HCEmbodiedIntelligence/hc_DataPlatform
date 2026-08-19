export const dataUploadRoutes = Object.freeze({
  legacyIndex: "/ingest/uploads",
  newUpload: "/ingest/uploads/new",
  records: "/ingest/uploads/records",
});

export const dataAnnotationRoutes = Object.freeze({
  legacyIndex: "/annotations",
  annotate: "/annotations/annotate",
  revisions: "/annotations/revisions",
  tagReview: "/annotations/tag-review",
});

function appendLocationState(
  pathname: string,
  search: string,
  hash: string,
  additions: Readonly<Record<string, string | undefined>> = {},
): string {
  const params = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  for (const [key, value] of Object.entries(additions)) {
    if (value === undefined) params.delete(key);
    else params.set(key, value);
  }
  const query = params.toString();
  return `${pathname}${query ? `?${query}` : ""}${hash}`;
}

export function buildUploadRecordsCompatibilityTarget(
  search: string,
  hash = "",
): string {
  return appendLocationState(dataUploadRoutes.records, search, hash);
}

export function buildAnnotationCompatibilityTarget(
  search: string,
  hash = "",
): string {
  return appendLocationState(dataAnnotationRoutes.annotate, search, hash);
}

export function buildCleaningCompatibilityTarget(
  search: string,
  hash = "",
  legacyDraftId?: string,
): string {
  return appendLocationState(dataAnnotationRoutes.revisions, search, hash, {
    legacyDraftId,
  });
}
