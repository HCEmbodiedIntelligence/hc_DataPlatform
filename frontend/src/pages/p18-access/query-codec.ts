import { defineQueryCodec } from "../../shared/routing/route-registry";
import type { AccessRequestStatus } from "./contracts";

export type AccessTab = "users" | "membership-requests" | "capability-requests";

export interface AccessSearch {
  readonly tab: AccessTab;
  readonly q?: string;
  readonly status: "ALL" | AccessRequestStatus;
  readonly accountState: "ALL" | "ACTIVE" | "DISABLED" | "DELETED";
  readonly accountRole: "ALL" | "USER" | "PLATFORM_ADMIN";
  readonly order: "recent" | "oldest";
  readonly page: number;
  readonly pageSize: 10 | 20;
  readonly requestId?: string;
  readonly drawer: "open" | "closed";
}

const defaults: AccessSearch = {
  tab: "membership-requests",
  status: "ALL",
  accountState: "ALL",
  accountRole: "ALL",
  order: "recent",
  page: 1,
  pageSize: 10,
  drawer: "closed",
};
const tabs = new Set<AccessTab>([
  "users",
  "membership-requests",
  "capability-requests",
]);
const statuses = new Set<AccessSearch["status"]>([
  "ALL",
  "PENDING",
  "APPROVED",
  "REJECTED",
  "WITHDRAWN",
  "REVOKED",
]);
const accountStates = new Set<AccessSearch["accountState"]>([
  "ALL",
  "ACTIVE",
  "DISABLED",
  "DELETED",
]);
const accountRoles = new Set<AccessSearch["accountRole"]>([
  "ALL",
  "USER",
  "PLATFORM_ADMIN",
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
      accountState: accountStates.has(
        sp.get("accountState") as AccessSearch["accountState"],
      )
        ? (sp.get("accountState") as AccessSearch["accountState"])
        : defaults.accountState,
      accountRole: accountRoles.has(
        sp.get("accountRole") as AccessSearch["accountRole"],
      )
        ? (sp.get("accountRole") as AccessSearch["accountRole"])
        : defaults.accountRole,
      order: sp.get("order") === "oldest" ? "oldest" : defaults.order,
      page: positiveInteger(sp.get("page"), defaults.page),
      pageSize: sp.get("pageSize") === "20" ? 20 : defaults.pageSize,
      drawer: sp.get("drawer") === "open" ? "open" : defaults.drawer,
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
    if (merged.accountState !== defaults.accountState)
      sp.set("accountState", merged.accountState);
    if (merged.accountRole !== defaults.accountRole)
      sp.set("accountRole", merged.accountRole);
    if (merged.order !== defaults.order) sp.set("order", merged.order);
    if (merged.page !== defaults.page) sp.set("page", String(merged.page));
    if (merged.pageSize !== defaults.pageSize)
      sp.set("pageSize", String(merged.pageSize));
    if (merged.requestId) sp.set("requestId", merged.requestId);
    if (merged.drawer !== defaults.drawer) sp.set("drawer", merged.drawer);
    return sp;
  },
});
