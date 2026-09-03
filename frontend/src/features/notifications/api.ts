import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import { useShellStore } from "../../shared/scope/shell-store";

export type AccountNotification = components["schemas"]["AccountNotification"];
export type AccountNotificationKind =
  components["schemas"]["AccountNotificationKind"];
export type AccountNotificationState =
  components["schemas"]["AccountNotificationState"];
export type AccountNotificationPage =
  components["schemas"]["AccountNotificationPage"];

const notificationKindSchema = z.enum([
  "MEMBERSHIP_APPROVED",
  "MEMBERSHIP_REJECTED",
  "MEMBERSHIP_REVOKED",
  "CAPABILITY_APPROVED",
  "CAPABILITY_REJECTED",
  "CAPABILITY_REVOKED",
  "COLLECTION_TASK_CLOSED",
  "COLLECTION_TASK_CANCELLED",
  "COLLECTION_TASK_REOPENED",
  "DATASET_VERSION_PUBLISHED",
]);
const notificationResourceTypeSchema = z.enum([
  "ACCESS_REQUEST",
  "COLLECTION_TASK",
  "DATASET_VERSION",
]);
const notificationStateSchema = z.enum(["UNREAD", "READ"]);
const instant = z.string().datetime({ offset: true });

const notificationWireSchema: z.ZodType<AccountNotification> = z
  .object({
    notification_id: z.string().uuid(),
    kind: notificationKindSchema,
    organization_id: z.string().min(1).max(256),
    project_id: z.string().min(1).max(256),
    resource_type: notificationResourceTypeSchema,
    resource_id: z.string().min(1).max(512),
    state: notificationStateSchema,
    created_at: instant,
    read_at: instant.nullable().optional(),
  })
  .strict();

const notificationPageWireSchema: z.ZodType<AccountNotificationPage> = z
  .object({
    items: z.array(notificationWireSchema),
    next_cursor: z.string().min(16).max(16_384).nullable().optional(),
  })
  .strict();

const unreadCountWireSchema = z
  .object({ unread_count: z.number().int().nonnegative() })
  .strict();

export const notificationQueryKeys = {
  unread: (principalId: string) =>
    ["account-notifications", principalId, "unread"] as const,
  page: (principalId: string, state: AccountNotificationState | null) =>
    ["account-notifications", principalId, "page", state ?? "ALL"] as const,
};

export async function getUnreadNotificationCount(
  signal?: AbortSignal,
): Promise<number> {
  const endpoint = "/account/notifications/unread-count";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(unreadCountWireSchema, raw, { endpoint }).unread_count;
}

export async function listAccountNotifications(
  state: AccountNotificationState | null,
  cursor?: string | null,
  signal?: AbortSignal,
): Promise<AccountNotificationPage> {
  const endpoint = "/account/notifications";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    query: {
      limit: 20,
      ...(state ? { state } : {}),
      ...(cursor ? { cursor } : {}),
    },
    ...(signal ? { signal } : {}),
  });
  return parseWire(notificationPageWireSchema, raw, { endpoint });
}

export async function markAccountNotificationRead(
  notificationId: string,
): Promise<void> {
  const endpoint = `/account/notifications/${encodeURIComponent(notificationId)}:read`;
  await request<void>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
  });
}

export function useUnreadNotificationCount() {
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  return useQuery({
    queryKey: notificationQueryKeys.unread(principalId ?? "signed-out"),
    enabled: principalId !== null,
    staleTime: 30_000,
    retry: 1,
    queryFn: ({ signal }) => getUnreadNotificationCount(signal),
  });
}
