import { z } from "zod";
import type { ActorSummary } from "../../entities/actor";
import { makeScopeKey, type Scope } from "../../entities/scope";

const SESSION_STORAGE_KEY = "hc-platform-current-session";

const actorSchema = z
  .object({
    actorId: z.string().trim().min(1),
    displayName: z.string().trim().min(1),
    avatarUrl: z.string().trim().min(1).optional(),
    roleIds: z.array(
      z.enum([
        "PROJECT_ADMIN",
        "PROJECT_DEVELOPER",
        "PROJECT_DATA_PROCESSOR",
      ]),
    ),
  })
  .strict();

const scopeSchema = z
  .object({
    organizationId: z.string(),
    projectId: z.string().trim().min(1).optional(),
    regionCode: z.string().trim().min(1).optional(),
  })
  .strict()
  .refine((scope) => scope.regionCode === undefined || scope.projectId !== undefined);

const persistedSessionSchema = z
  .object({
    version: z.literal(1),
    principal: actorSchema,
    sessionToken: z.string().min(1),
    scope: scopeSchema.nullable(),
  })
  .strict();

export interface PersistedSession {
  readonly principal: ActorSummary;
  readonly sessionToken: string;
  readonly scope: Scope | null;
}

function browserSessionStorage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

function removePersistedSession(storage: Storage): void {
  try {
    storage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Ignore storage denial; callers still fail closed in memory.
  }
}

export function readPersistedSession(
  storage: Storage | null = browserSessionStorage(),
): PersistedSession | null {
  if (storage === null) return null;
  try {
    const raw = storage.getItem(SESSION_STORAGE_KEY);
    if (raw === null) return null;
    const parsed = persistedSessionSchema.safeParse(JSON.parse(raw));
    if (!parsed.success) {
      removePersistedSession(storage);
      return null;
    }
    if (parsed.data.scope !== null) makeScopeKey(parsed.data.scope);
    return {
      principal: parsed.data.principal,
      sessionToken: parsed.data.sessionToken,
      scope: parsed.data.scope,
    };
  } catch {
    removePersistedSession(storage);
    return null;
  }
}

export function writePersistedSession(
  principal: ActorSummary | null,
  sessionToken: string | null,
  scope: Scope | null,
  storage: Storage | null = browserSessionStorage(),
): void {
  if (storage === null) return;
  try {
    if (principal === null || sessionToken === null) {
      removePersistedSession(storage);
      return;
    }
    storage.setItem(
      SESSION_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        principal,
        sessionToken,
        scope,
      }),
    );
  } catch {
    // Storage can be unavailable in hardened browser contexts. The in-memory
    // session remains usable for the lifetime of the current document.
  }
}
