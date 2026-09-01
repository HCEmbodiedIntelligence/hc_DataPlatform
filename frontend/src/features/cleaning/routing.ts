const ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{1,127}$/;
const CURSOR_MAX_LENGTH = 2048;

export const MANUAL_ISSUE_SORTS = [
  "updatedAtDesc",
  "updatedAtAsc",
  "severityDesc",
  "createdAtDesc",
] as const;
export type ManualIssueSort = (typeof MANUAL_ISSUE_SORTS)[number];

export type ManualIssuesRouteParams = {
  readonly q?: string;
  readonly datasetId?: string;
  readonly versionId?: string;
  readonly episodeId?: string;
  readonly issueId?: string;
  readonly status?: readonly ("OPEN" | "IN_PROGRESS" | "RESOLVED")[];
  readonly issueType?: readonly string[];
  readonly severity?: readonly ("LOW" | "MEDIUM" | "HIGH" | "CRITICAL")[];
  readonly discoverySource?: readonly (
    | "AUTO_QC"
    | "DATA_VIEWER"
    | "ANNOTATOR"
    | "REVIEWER"
  )[];
  readonly assigneeId?: string;
  readonly sort?: ManualIssueSort;
  readonly after?: string;
  readonly before?: string;
  readonly limit?: 20 | 50 | 100;
  readonly returnTo?: string;
};

export type ManualIssueRawDiagnosticRouteParams = {
  readonly uploadId: string;
  readonly returnTo?: string;
};

export type CleaningDraftScope =
  | "mine"
  | "review"
  | "returned"
  | "submitted"
  | "all"
  | "actionable";
export type CleaningDraftStatusFilter =
  | "active"
  | "submitted"
  | "failed"
  | "archived";
export type CleaningDraftSort =
  | "updatedAtDesc"
  | "updatedAtAsc"
  | "createdAtDesc"
  | "reuseRatioDesc"
  | "effectiveDurationDesc";

export type CleaningDraftsRouteParams = {
  readonly scope?: CleaningDraftScope;
  readonly status?: CleaningDraftStatusFilter;
  readonly q?: string;
  readonly datasetId?: string;
  readonly baseVersionId?: string;
  readonly episodeId?: string;
  readonly robotId?: string;
  readonly creatorId?: string;
  readonly updatedFrom?: string;
  readonly updatedTo?: string;
  readonly previewStatus?: readonly string[];
  readonly commitStatus?: readonly string[];
  readonly versionReviewStatus?: readonly (
    | "REVIEWING"
    | "READY"
    | "RETURNED"
  )[];
  readonly findingType?: readonly string[];
  readonly findingSeverity?: readonly string[];
  readonly projection?: "pendingReview" | "returned";
  readonly sort?: CleaningDraftSort;
  readonly after?: string;
  readonly before?: string;
  readonly limit?: 20 | 50 | 100;
  readonly draftId?: string;
};

export type CleaningWorkbenchRouteParams = {
  readonly draftId: string;
  readonly t?: string;
  readonly layout?: string;
  readonly streamId?: string;
  readonly windowStartNs?: string;
  readonly windowEndNs?: string;
  readonly operationId?: string;
  readonly findingId?: string;
  readonly compare?: "source" | "cleaned" | "ab";
  readonly returnTo?: string;
};

export interface ManualIssuesSearch
  extends Omit<ManualIssuesRouteParams, "returnTo"> {
  readonly sort: ManualIssueSort;
  readonly limit: 20 | 50 | 100;
  readonly returnTo?: string;
}

function asSearchParams(input: string | URLSearchParams): URLSearchParams {
  if (input instanceof URLSearchParams) return new URLSearchParams(input);
  return new URLSearchParams(input.startsWith("?") ? input.slice(1) : input);
}

function cleanText(value: string | null, max: number): string | undefined {
  if (value === null) return undefined;
  const normalized = value.trim().normalize("NFC");
  return normalized.length > 0 &&
    normalized.length <= max &&
    !/\p{Cc}/u.test(normalized)
    ? normalized
    : undefined;
}

function cleanId(value: string | null): string | undefined {
  const cleaned = cleanText(value, 128);
  return cleaned !== undefined && ID_PATTERN.test(cleaned)
    ? cleaned
    : undefined;
}

function cleanCursor(value: string | null): string | undefined {
  const cleaned = cleanText(value, CURSOR_MAX_LENGTH);
  return cleaned?.includes("/") || cleaned?.includes("\\")
    ? undefined
    : cleaned;
}

function enumValue<T extends string>(
  value: string | null,
  values: readonly T[],
): T | undefined {
  return value !== null && (values as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

function enumList<T extends string>(
  params: URLSearchParams,
  key: string,
  values: readonly T[],
): T[] | undefined {
  const rank = new Map(values.map((value, index) => [value, index]));
  const result = [
    ...new Set(
      params.getAll(key).filter((value): value is T => rank.has(value as T)),
    ),
  ].sort((a, b) => rank.get(a)! - rank.get(b)!);
  return result.length > 0 ? result : undefined;
}

function stringList(
  params: URLSearchParams,
  key: string,
): string[] | undefined {
  const values = [
    ...new Set(
      params
        .getAll(key)
        .map((value) => cleanText(value, 96))
        .filter(Boolean) as string[],
    ),
  ].sort((a, b) => a.localeCompare(b, "en"));
  return values.length > 0 ? values : undefined;
}

export function isSafeAppRelativeUrl(value: string): boolean {
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    /\p{Cc}/u.test(value)
  )
    return false;
  try {
    const parsed = new URL(value, "https://local.invalid");
    return parsed.origin === "https://local.invalid" && parsed.hash === "";
  } catch {
    return false;
  }
}

export function safeReturnTo(
  value: string | null | undefined,
  expected?: {
    readonly datasetId?: string;
    readonly versionId?: string;
    readonly episodeId?: string;
  },
): string | undefined {
  if (!value || !isSafeAppRelativeUrl(value)) return undefined;
  const url = new URL(value, "https://local.invalid");
  const match = url.pathname.match(
    /^\/datasets\/([^/]+)\/versions\/([^/]+)\/episodes\/([^/]+)\/view$/,
  );
  if (!match) return undefined;
  const datasetId = decodeURIComponent(match[1]!);
  const versionId = decodeURIComponent(match[2]!);
  const episodeId = decodeURIComponent(match[3]!);
  if (![datasetId, versionId, episodeId].every((id) => ID_PATTERN.test(id)))
    return undefined;
  if (expected?.datasetId && datasetId !== expected.datasetId) return undefined;
  if (expected?.versionId && versionId !== expected.versionId) return undefined;
  if (expected?.episodeId && episodeId !== expected.episodeId) return undefined;
  return `${url.pathname}${url.search}`;
}

function appendAll(
  params: URLSearchParams,
  key: string,
  values: readonly string[] | undefined,
): void {
  values?.forEach((value) => params.append(key, value));
}

function buildManualIssueSearch(
  input: ManualIssuesRouteParams,
): URLSearchParams {
  const params = new URLSearchParams();
  const q = cleanText(input.q ?? null, 200);
  const datasetId = cleanId(input.datasetId ?? null);
  const versionId = cleanId(input.versionId ?? null);
  const episodeId = cleanId(input.episodeId ?? null);
  const issueId = cleanId(input.issueId ?? null);
  if (q) params.set("q", q);
  if (datasetId) params.set("datasetId", datasetId);
  if (versionId) params.set("versionId", versionId);
  if (episodeId) params.set("episodeId", episodeId);
  if (issueId) params.set("issueId", issueId);
  appendAll(
    params,
    "status",
    input.status ? [...new Set(input.status)].sort() : undefined,
  );
  appendAll(
    params,
    "issueType",
    input.issueType ? [...new Set(input.issueType)].sort() : undefined,
  );
  appendAll(
    params,
    "severity",
    input.severity ? [...new Set(input.severity)].sort() : undefined,
  );
  appendAll(
    params,
    "source",
    input.discoverySource
      ? [...new Set(input.discoverySource)].sort()
      : undefined,
  );
  if (input.assigneeId && cleanId(input.assigneeId))
    params.set("assigneeId", input.assigneeId);
  if (input.sort && input.sort !== "updatedAtDesc")
    params.set("sort", input.sort);
  if (input.limit && input.limit !== 50)
    params.set("limit", String(input.limit));
  if (input.after && !input.before) params.set("after", input.after);
  if (input.before && !input.after) params.set("before", input.before);
  const returnTo = safeReturnTo(input.returnTo, {
    datasetId,
    versionId,
    episodeId,
  });
  if (returnTo) params.set("returnTo", returnTo);
  return params;
}

function buildManualIssueRawDiagnostic(
  input: ManualIssueRawDiagnosticRouteParams,
): string {
  const uploadId = cleanText(input.uploadId, 256);
  if (
    !uploadId ||
    uploadId === "latest" ||
    uploadId === "current" ||
    /[/\\?#]/u.test(uploadId)
  ) {
    throw new TypeError("Invalid upload session ID");
  }
  const params = new URLSearchParams();
  if (input.returnTo && isSafeAppRelativeUrl(input.returnTo)) {
    const target = new URL(input.returnTo, "https://local.invalid");
    if (target.pathname === "/manual/issues") {
      params.set("returnTo", `${target.pathname}${target.search}`);
    }
  }
  const query = params.toString();
  const path = `/manual/issues/raw-diagnostic/${encodeURIComponent(uploadId)}`;
  return query ? `${path}?${query}` : path;
}

export const manualIssuesQueryCodec = {
  parse(input: string | URLSearchParams): ManualIssuesSearch {
    const params = asSearchParams(input);
    const datasetId = cleanId(params.get("datasetId"));
    const versionId = cleanId(params.get("versionId"));
    const episodeId = cleanId(params.get("episodeId"));
    let after = cleanCursor(params.get("after"));
    let before = cleanCursor(params.get("before"));
    if (after && before) [after, before] = [undefined, undefined];
    const limit = enumValue(params.get("limit"), ["20", "50", "100"] as const);
    return {
      q: cleanText(params.get("q"), 200),
      datasetId,
      versionId,
      episodeId,
      issueId: cleanId(params.get("issueId")),
      status: enumList(params, "status", [
        "OPEN",
        "IN_PROGRESS",
        "RESOLVED",
      ] as const),
      issueType: stringList(params, "issueType"),
      severity: enumList(params, "severity", [
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
      ] as const),
      discoverySource: enumList(params, "source", [
        "AUTO_QC",
        "DATA_VIEWER",
        "ANNOTATOR",
        "REVIEWER",
      ] as const),
      assigneeId: cleanId(params.get("assigneeId")),
      sort:
        enumValue(params.get("sort"), MANUAL_ISSUE_SORTS) ?? "updatedAtDesc",
      after,
      before,
      limit: limit ? (Number.parseInt(limit, 10) as 20 | 50 | 100) : 50,
      returnTo: safeReturnTo(params.get("returnTo"), {
        datasetId,
        versionId,
        episodeId,
      }),
    };
  },
  build(input: ManualIssuesRouteParams): URLSearchParams {
    return buildManualIssueSearch(input);
  },
  canonicalize(input: string | URLSearchParams): string {
    return buildManualIssueSearch(this.parse(input)).toString();
  },
  withChanges(
    current: ManualIssuesSearch,
    changes: Partial<ManualIssuesRouteParams>,
  ): ManualIssuesSearch {
    const paginationDimensions = [
      "q",
      "datasetId",
      "versionId",
      "episodeId",
      "status",
      "issueType",
      "severity",
      "discoverySource",
      "assigneeId",
      "sort",
      "limit",
    ] as const;
    const cursorMustClear = paginationDimensions.some(
      (key) =>
        key in changes &&
        JSON.stringify(current[key]) !== JSON.stringify(changes[key]),
    );
    return this.parse(
      buildManualIssueSearch({
        ...current,
        ...changes,
        ...(cursorMustClear ? { after: undefined, before: undefined } : {}),
      }),
    );
  },
} as const;

function buildCleaningDrafts(input: CleaningDraftsRouteParams = {}): string {
  const params = new URLSearchParams();
  const scope =
    input.projection === "pendingReview"
      ? "review"
      : input.projection === "returned"
        ? "returned"
        : input.scope;
  const reviewStatus =
    input.projection === "pendingReview"
      ? ["REVIEWING"]
      : input.projection === "returned"
        ? ["RETURNED"]
        : input.versionReviewStatus;
  if (scope && scope !== "mine") params.set("scope", scope);
  if (input.status && input.status !== "active")
    params.set("status", input.status);
  const q = cleanText(input.q ?? null, 200);
  if (q) params.set("q", q);
  for (const key of [
    "datasetId",
    "baseVersionId",
    "episodeId",
    "robotId",
    "creatorId",
    "draftId",
  ] as const) {
    const value = cleanId(input[key] ?? null);
    if (value) params.set(key, value);
  }
  appendAll(
    params,
    "previewStatus",
    input.previewStatus ? [...new Set(input.previewStatus)].sort() : undefined,
  );
  appendAll(
    params,
    "commitStatus",
    input.commitStatus ? [...new Set(input.commitStatus)].sort() : undefined,
  );
  appendAll(
    params,
    "versionReviewStatus",
    reviewStatus ? [...new Set(reviewStatus)].sort() : undefined,
  );
  appendAll(
    params,
    "findingType",
    input.findingType ? [...new Set(input.findingType)].sort() : undefined,
  );
  appendAll(
    params,
    "findingSeverity",
    input.findingSeverity
      ? [...new Set(input.findingSeverity)].sort()
      : undefined,
  );
  if (input.updatedFrom && input.updatedTo) {
    params.set("updatedFrom", input.updatedFrom);
    params.set("updatedTo", input.updatedTo);
  }
  if (input.sort && input.sort !== "updatedAtDesc")
    params.set("sort", input.sort);
  if (input.limit && input.limit !== 50)
    params.set("limit", String(input.limit));
  if (input.after && !input.before) params.set("after", input.after);
  if (input.before && !input.after) params.set("before", input.before);
  return "/annotations/revisions";
}

function buildCleaningWorkbench(input: CleaningWorkbenchRouteParams): string {
  if (!ID_PATTERN.test(input.draftId))
    throw new TypeError("Invalid CleaningDraft ID");
  const params = new URLSearchParams();
  for (const key of [
    "t",
    "layout",
    "streamId",
    "operationId",
    "findingId",
  ] as const) {
    const value = cleanText(input[key] ?? null, 256);
    if (value) params.set(key, value);
  }
  if (
    input.windowStartNs &&
    input.windowEndNs &&
    /^\d+$/.test(input.windowStartNs) &&
    /^\d+$/.test(input.windowEndNs)
  ) {
    params.set("windowStartNs", input.windowStartNs);
    params.set("windowEndNs", input.windowEndNs);
  }
  if (input.compare && input.compare !== "source")
    params.set("compare", input.compare);
  if (input.returnTo && isSafeAppRelativeUrl(input.returnTo))
    params.set("returnTo", input.returnTo);
  params.set("legacyDraftId", input.draftId);
  const query = params.toString();
  return `/annotations/revisions?${query}`;
}

export const routes = {
  manualIssues: {
    build(input: ManualIssuesRouteParams = {}): string {
      const query = buildManualIssueSearch(input).toString();
      return query ? `/manual/issues?${query}` : "/manual/issues";
    },
  },
  manualIssueRawDiagnostic: { build: buildManualIssueRawDiagnostic },
  cleaningDrafts: { build: buildCleaningDrafts },
  cleaningWorkbench: { build: buildCleaningWorkbench },
} as const;

export {
  configureManualCleaningCommandTransport,
  createManualIssueCommand,
} from "./api/manual-issues.commands";
export type {
  CreateManualIssueCommandInput,
  ManualCleaningCommandTransport,
} from "./api/manual-issues.commands";
