export const PAGE_STATE_KINDS = [
  "loading",
  "refreshing",
  "partial",
  "empty",
  "filtered-empty",
  "error",
  "forbidden",
  "not-found",
  "gone",
  "conflict",
  "rate-limited",
  "offline",
  "contract-mismatch",
  "unknown",
  "feature-unavailable",
] as const;

export type PageStateKind = (typeof PAGE_STATE_KINDS)[number];

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export type MetricState =
  | "ready"
  | "loading"
  | "unknown"
  | "forbidden"
  | "error";
