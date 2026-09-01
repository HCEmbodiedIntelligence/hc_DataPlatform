import { defineQueryCodec } from "../../shared/routing/route-registry";

export const lifecyclePolicyStates = [
  "ALL",
  "DRAFT",
  "ENABLED",
  "PAUSED",
] as const;
export type LifecyclePolicyStateFilter = (typeof lifecyclePolicyStates)[number];

export const lifecycleObjectRoles = [
  "ALL",
  "RAW",
  "MANIFEST",
  "PUBLISHED_MANIFEST",
  "REBUILDABLE_DERIVATIVE",
  "OTHER",
] as const;
export type LifecycleObjectRoleFilter = (typeof lifecycleObjectRoles)[number];

export interface StorageLifecycleSearch {
  readonly query: string;
  readonly state: LifecyclePolicyStateFilter;
  readonly role: LifecycleObjectRoleFilter;
  readonly policyCursor?: string;
  readonly auditCursor?: string;
  readonly limit: 20 | 50 | 100;
}

const defaults: StorageLifecycleSearch = {
  query: "",
  state: "ALL",
  role: "ALL",
  limit: 50,
};

function cursor(params: URLSearchParams, key: string): string | undefined {
  return params.get(key)?.trim().slice(0, 16_384) || undefined;
}

function isPolicyState(
  value: string | null,
): value is LifecyclePolicyStateFilter {
  return lifecyclePolicyStates.some((item) => item === value);
}

function isObjectRole(
  value: string | null,
): value is LifecycleObjectRoleFilter {
  return lifecycleObjectRoles.some((item) => item === value);
}

export const storageLifecycleQueryCodec =
  defineQueryCodec<StorageLifecycleSearch>({
    defaults,
    parse(params) {
      const rawLimit = Number(params.get("limit"));
      const limit = rawLimit === 20 || rawLimit === 100 ? rawLimit : 50;
      const rawState = params.get("state");
      const rawRole = params.get("role");
      const state = isPolicyState(rawState) ? rawState : "ALL";
      const role = isObjectRole(rawRole) ? rawRole : "ALL";
      const query = params.get("q")?.trim().slice(0, 256) ?? "";
      const policyCursor = cursor(params, "policy_cursor");
      const auditCursor = cursor(params, "audit_cursor");
      return {
        query,
        state,
        role,
        limit,
        ...(policyCursor ? { policyCursor } : {}),
        ...(auditCursor ? { auditCursor } : {}),
      };
    },
    build(value) {
      const merged = { ...defaults, ...value };
      const params = new URLSearchParams();
      if (merged.query) params.set("q", merged.query);
      if (merged.state !== "ALL") params.set("state", merged.state);
      if (merged.role !== "ALL") params.set("role", merged.role);
      if (merged.policyCursor) params.set("policy_cursor", merged.policyCursor);
      if (merged.auditCursor) params.set("audit_cursor", merged.auditCursor);
      if (merged.limit !== defaults.limit)
        params.set("limit", String(merged.limit));
      return params;
    },
    cursorResetKeys: ["query", "state", "role", "limit"],
  });

export function updateLifecycleSearch(
  current: StorageLifecycleSearch,
  patch: Partial<StorageLifecycleSearch>,
): StorageLifecycleSearch {
  const policyFilterChanged =
    ("query" in patch && patch.query !== current.query) ||
    ("state" in patch && patch.state !== current.state) ||
    ("role" in patch && patch.role !== current.role) ||
    ("limit" in patch && patch.limit !== current.limit);
  const auditWindowChanged = "limit" in patch && patch.limit !== current.limit;
  return storageLifecycleQueryCodec.normalize({
    ...current,
    ...patch,
    ...(policyFilterChanged ? { policyCursor: undefined } : {}),
    ...(auditWindowChanged ? { auditCursor: undefined } : {}),
  });
}
