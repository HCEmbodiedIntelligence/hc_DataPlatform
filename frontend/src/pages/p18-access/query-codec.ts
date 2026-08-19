import { defineQueryCodec } from "../../shared/routing/route-registry";
import type { AccessRequestStatus } from "./contracts";

export type AccessTab = "membership-requests" | "capability-requests";

export interface AccessSearch {
  readonly tab: AccessTab;
  readonly q?: string;
  readonly status: "ALL" | AccessRequestStatus;
  readonly order: "recent" | "oldest";
  readonly page: number;
  readonly pageSize: 10 | 20;
  readonly requestId?: string;
  readonly drawer: "open" | "closed";
}

const defaults: AccessSearch = {
  tab: "membership-requests",
  status: "ALL",
  order: "recent",
  page: 1,
  pageSize: 10,
  drawer: "open",
};
const tabs = new Set<AccessTab>(["membership-requests", "capability-requests"]);
const statuses = new Set<AccessSearch["status"]>([
  "ALL",
  "PENDING",
  "APPROVED",
  "REJECTED",
  "WITHDRAWN",
  "REVOKED",
]);

function positiveInteger(value: string | null, fallback: number): number {
  if (!value || !/^\d+$/u.test(value)) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export const accessQueryCodec = defineQueryCodec<AccessSearch>({
  defaults,
  parse(sp) {
    const tab = tabs.has(sp.get("tab") as AccessTab)
      ? (sp.get("tab") as AccessTab)
      : defaults.tab;
    const status = statuses.has(sp.get("status") as AccessSearch["status"])
      ? (sp.get("status") as AccessSearch["status"])
      : defaults.status;
    const q = sp.get("q")?.trim().slice(0, 100);
    const requestId = sp.get("requestId")?.trim().slice(0, 128);
    return {
      tab,
      status,
      order: sp.get("order") === "oldest" ? "oldest" : defaults.order,
      page: positiveInteger(sp.get("page"), defaults.page),
      pageSize: sp.get("pageSize") === "20" ? 20 : defaults.pageSize,
      drawer: sp.get("drawer") === "closed" ? "closed" : defaults.drawer,
      ...(q ? { q } : {}),
      ...(requestId ? { requestId } : {}),
    };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    if (merged.tab !== defaults.tab) sp.set("tab", merged.tab);
    if (merged.q) sp.set("q", merged.q);
    if (merged.status !== defaults.status) sp.set("status", merged.status);
    if (merged.order !== defaults.order) sp.set("order", merged.order);
    if (merged.page !== defaults.page) sp.set("page", String(merged.page));
    if (merged.pageSize !== defaults.pageSize)
      sp.set("pageSize", String(merged.pageSize));
    if (merged.requestId) sp.set("requestId", merged.requestId);
    if (merged.drawer !== defaults.drawer) sp.set("drawer", merged.drawer);
    return sp;
  },
});
