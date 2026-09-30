import type { AnnotationTaskDisplayState } from "../../entities/annotation-task";

export type AnnotationQueue = "assigned_to_me" | "claimable";
export type AnnotationQueueSort =
  | "priority_desc"
  | "due_at_asc"
  | "updated_at_desc";
export type AnnotationQueueStage =
  | "DRAFT"
  | "SUBMITTED"
  | "NEEDS_REVISION"
  | "APPROVED"
  | "REJECTED";

export interface AnnotationQueueSearch {
  readonly queue: AnnotationQueue;
  readonly stage: AnnotationQueueStage;
  readonly states: readonly AnnotationTaskDisplayState[];
  readonly datasetId?: string;
  readonly schemaVersionId?: string;
  readonly assigneeId?: string;
  readonly q?: string;
  readonly sort: AnnotationQueueSort;
  readonly after?: string;
  readonly before?: string;
  readonly page: number;
  readonly limit: 20 | 50 | 100;
}

export interface AnnotationTaskSearch {
  readonly t?: string;
  readonly selectedAnnotationId?: string;
  readonly panel?: "labels" | "objects" | "issues" | "feedback";
  readonly zoomStartNs?: string;
  readonly zoomEndNs?: string;
  readonly returnTo?: string;
}

const queueValues = new Set<AnnotationQueue>(["assigned_to_me", "claimable"]);
const sortValues = new Set<AnnotationQueueSort>([
  "priority_desc",
  "due_at_asc",
  "updated_at_desc",
]);
const stageValues = new Set<AnnotationQueueStage>([
  "DRAFT",
  "SUBMITTED",
  "NEEDS_REVISION",
  "APPROVED",
  "REJECTED",
]);
const stateValues = new Set<AnnotationTaskDisplayState>([
  "UNASSIGNED",
  "ASSIGNED",
  "IN_PROGRESS",
  "BLOCKED",
  "SUBMITTED",
  "RETURNED",
  "COMPLETED",
  "CANCELLED",
  "STALE",
]);
const panelValues = new Set<NonNullable<AnnotationTaskSearch["panel"]>>([
  "labels",
  "objects",
  "issues",
  "feedback",
]);
const unsigned = /^(0|[1-9][0-9]*)$/;
const decimal = /^(0|[1-9][0-9]*)(\.[0-9]{1,9})?$/;

function normalizedText(value: string | null, max = 100): string | undefined {
  const result = value?.normalize("NFC").trim().slice(0, max);
  return result || undefined;
}

function hasControlCharacter(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  });
}

function safeReturnTo(value: string | null): string | undefined {
  if (
    !value ||
    value.length > 2048 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    hasControlCharacter(value)
  )
    return undefined;
  try {
    const parsed = new URL(value, "https://application.invalid");
    if (parsed.origin !== "https://application.invalid" || parsed.hash)
      return undefined;
    const allowed =
      parsed.pathname === "/annotations/annotate" ||
      parsed.pathname === "/annotations/tag-review" ||
      parsed.pathname === "/annotations/revisions" ||
      parsed.pathname === "/manual/issues" ||
      /^\/datasets\/[^/]+\/versions\/[^/]+\/episodes\/[^/]+\/view$/.test(
        parsed.pathname,
      );
    return allowed ? `${parsed.pathname}${parsed.search}` : undefined;
  } catch {
    return undefined;
  }
}

export const annotationQueueQueryCodec = {
  parse(
    input: string | URLSearchParams,
    canAssign = false,
    defaultStage: AnnotationQueueStage = "DRAFT",
  ): AnnotationQueueSearch {
    const params =
      typeof input === "string"
        ? new URLSearchParams(input.startsWith("?") ? input.slice(1) : input)
        : input;
    const queueValue = params.get("queue") as AnnotationQueue | null;
    const sortValue = params.get("sort") as AnnotationQueueSort | null;
    const stageValue = params.get("stage") as AnnotationQueueStage | null;
    const limitValue = Number(params.get("limit"));
    const pageValue = Number(params.get("page"));
    const after = normalizedText(params.get("after"), 1024);
    const before = normalizedText(params.get("before"), 1024);
    const states = (params.get("state") ?? "")
      .split(",")
      .filter((state): state is AnnotationTaskDisplayState =>
        stateValues.has(state as AnnotationTaskDisplayState),
      )
      .sort();
    return {
      queue:
        queueValue && queueValues.has(queueValue)
          ? queueValue
          : "assigned_to_me",
      stage:
        stageValue && stageValues.has(stageValue) ? stageValue : defaultStage,
      states,
      ...(normalizedText(params.get("datasetId"), 128)
        ? { datasetId: normalizedText(params.get("datasetId"), 128)! }
        : {}),
      ...(normalizedText(params.get("schemaVersionId"), 128)
        ? {
            schemaVersionId: normalizedText(
              params.get("schemaVersionId"),
              128,
            )!,
          }
        : {}),
      ...(canAssign && normalizedText(params.get("assigneeId"), 128)
        ? { assigneeId: normalizedText(params.get("assigneeId"), 128)! }
        : {}),
      ...(normalizedText(params.get("q"))
        ? { q: normalizedText(params.get("q"))! }
        : {}),
      sort:
        sortValue && sortValues.has(sortValue) ? sortValue : "priority_desc",
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
      page: Number.isSafeInteger(pageValue) && pageValue > 0 ? pageValue : 1,
      limit: limitValue === 50 || limitValue === 100 ? limitValue : 20,
    };
  },
  build(search: AnnotationQueueSearch): string {
    const params = new URLSearchParams();
    if (search.queue !== "assigned_to_me") params.set("queue", search.queue);
    if (search.stage !== "DRAFT") params.set("stage", search.stage);
    if (search.states.length)
      params.set("state", [...search.states].sort().join(","));
    if (search.datasetId) params.set("datasetId", search.datasetId);
    if (search.schemaVersionId)
      params.set("schemaVersionId", search.schemaVersionId);
    if (search.assigneeId) params.set("assigneeId", search.assigneeId);
    if (search.q)
      params.set("q", search.q.normalize("NFC").trim().slice(0, 100));
    if (search.sort !== "priority_desc") params.set("sort", search.sort);
    if (search.after) params.set("after", search.after);
    else if (search.before) params.set("before", search.before);
    if (search.page > 1) params.set("page", String(search.page));
    if (search.limit !== 20) params.set("limit", String(search.limit));
    return params.toString();
  },
};

export const annotationTaskQueryCodec = {
  parse(input: string | URLSearchParams): AnnotationTaskSearch {
    const params =
      typeof input === "string"
        ? new URLSearchParams(input.startsWith("?") ? input.slice(1) : input)
        : input;
    const t = params.get("t");
    const panel = params.get("panel") as AnnotationTaskSearch["panel"];
    const start = params.get("zoomStartNs");
    const end = params.get("zoomEndNs");
    const validRange =
      !!start &&
      !!end &&
      unsigned.test(start) &&
      unsigned.test(end) &&
      BigInt(start) < BigInt(end);
    return {
      ...(t && decimal.test(t) ? { t } : {}),
      ...(normalizedText(params.get("selectedAnnotationId"), 128)
        ? {
            selectedAnnotationId: normalizedText(
              params.get("selectedAnnotationId"),
              128,
            )!,
          }
        : {}),
      ...(panel && panelValues.has(panel) ? { panel } : {}),
      ...(validRange ? { zoomStartNs: start, zoomEndNs: end } : {}),
      ...(safeReturnTo(params.get("returnTo"))
        ? { returnTo: safeReturnTo(params.get("returnTo"))! }
        : {}),
    };
  },
  build(search: AnnotationTaskSearch): string {
    const params = new URLSearchParams();
    if (search.t && decimal.test(search.t)) params.set("t", search.t);
    if (search.selectedAnnotationId)
      params.set("selectedAnnotationId", search.selectedAnnotationId);
    if (search.panel) params.set("panel", search.panel);
    if (
      search.zoomStartNs &&
      search.zoomEndNs &&
      unsigned.test(search.zoomStartNs) &&
      unsigned.test(search.zoomEndNs) &&
      BigInt(search.zoomStartNs) < BigInt(search.zoomEndNs)
    ) {
      params.set("zoomStartNs", search.zoomStartNs);
      params.set("zoomEndNs", search.zoomEndNs);
    }
    if (search.returnTo && safeReturnTo(search.returnTo))
      params.set("returnTo", search.returnTo);
    return params.toString();
  },
};
