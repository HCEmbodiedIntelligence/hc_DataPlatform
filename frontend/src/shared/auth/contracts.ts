import type { ScopeKey } from "../scope/model";

export interface ActorSummary {
  actorId: string;
  displayName: string;
  avatarUrl?: string;
  roleIds: readonly ProjectRoleId[];
}

export type ProjectRoleId =
  | "PROJECT_ADMIN"
  | "PROJECT_DEVELOPER"
  | "PROJECT_DATA_PROCESSOR";

export interface AuthorizationSnapshot {
  scopeKey: ScopeKey;
  roleVersion: string;
  /** Exact capability keys returned by the runtime bootstrap. */
  capabilities: readonly string[];
  fetchedAt: string;
  expiresAt?: string;
}
