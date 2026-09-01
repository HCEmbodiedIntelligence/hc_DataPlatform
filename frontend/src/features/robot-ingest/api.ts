import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";
import {
  credentialSummarySchema,
  issuedCredentialSchema,
  robotAttemptListSchema,
  robotEpisodeResultListSchema,
  robotIdentityEnvelopeSchema,
  robotIdentityListSchema,
  robotStatisticsSchema,
  robotUploadListSchema,
  type RobotIdentity,
  type RobotUploadPolicy,
} from "./model";

function root(scope: IngestScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/robot-ingest`;
}

function parse<S>(schema: S, raw: unknown, endpoint: string) {
  return parseWire(schema as never, raw, {
    endpoint,
    schemaVersion: "robot-ingest.v1",
  }) as S extends { _output: infer Output } ? Output : never;
}

function identityRoot(scope: IngestScope): string {
  return `${root(scope)}/identities`;
}

function identityPath(scope: IngestScope, identityId: string): string {
  return `${identityRoot(scope)}/${encodeURIComponent(identityId)}`;
}

const queryRoot = ["robot-ingest"] as const;

export async function listRobotIdentities(
  scope: IngestScope,
  signal?: AbortSignal,
) {
  const path = identityRoot(scope);
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    signal,
    cache: "no-store",
  });
  return parse(robotIdentityListSchema, raw, path);
}

export async function getRobotIdentity(
  scope: IngestScope,
  identityId: string,
  signal?: AbortSignal,
) {
  const path = identityPath(scope, identityId);
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    signal,
    cache: "no-store",
  });
  return parse(robotIdentityEnvelopeSchema, raw, path);
}

export async function listRobotCredentials(
  scope: IngestScope,
  identityId: string,
  signal?: AbortSignal,
) {
  const path = `${identityPath(scope, identityId)}/credentials`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    signal,
    cache: "no-store",
  });
  return parse(credentialSummarySchema.array(), raw, path);
}

export async function listRobotUploads(
  scope: IngestScope,
  robotId: string,
  signal?: AbortSignal,
) {
  const path = `${root(scope)}/uploads`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    query: { robotId },
    signal,
    cache: "no-store",
  });
  return parse(robotUploadListSchema, raw, path);
}

export async function listRobotAttempts(
  scope: IngestScope,
  robotId: string,
  signal?: AbortSignal,
) {
  const path = `${root(scope)}/attempts`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    query: { robotId },
    signal,
    cache: "no-store",
  });
  return parse(robotAttemptListSchema, raw, path);
}

export async function listRobotUploadEpisodes(
  scope: IngestScope,
  uploadId: string,
  signal?: AbortSignal,
) {
  const path = `${root(scope)}/uploads/${encodeURIComponent(uploadId)}/episodes`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    signal,
    cache: "no-store",
  });
  return parse(robotEpisodeResultListSchema, raw, path);
}

export async function getRobotStatistics(
  scope: IngestScope,
  robotId: string,
  filters: {
    readonly collectionTaskId?: string;
    readonly sourceFormat?: string;
    readonly createdFrom?: string;
    readonly createdTo?: string;
  } = {},
  signal?: AbortSignal,
) {
  const path = `${root(scope)}/robots/${encodeURIComponent(robotId)}/statistics`;
  const raw = await request<unknown>({
    method: "GET",
    path,
    scope,
    query: filters,
    signal,
    cache: "no-store",
  });
  return parse(robotStatisticsSchema, raw, path);
}

async function mutateIdentity(options: {
  scope: IngestScope;
  identityId?: string;
  operation: "create" | "update" | "enable" | "disable";
  body?: unknown;
}) {
  const base = identityRoot(options.scope);
  const path =
    options.operation === "create"
      ? base
      : options.operation === "update"
        ? `${base}/${encodeURIComponent(options.identityId ?? "")}`
        : `${base}/${encodeURIComponent(options.identityId ?? "")}:${options.operation}`;
  const raw = await request<unknown>({
    method: options.operation === "update" ? "PATCH" : "POST",
    path,
    scope: options.scope,
    body: options.body,
    cache: "no-store",
  });
  return parse(robotIdentityEnvelopeSchema, raw, path);
}

async function issueCredential(options: {
  scope: IngestScope;
  identityId: string;
  rotate: boolean;
  expiresAt: string | null;
}) {
  const base = identityPath(options.scope, options.identityId);
  const path = options.rotate
    ? `${base}:rotate-credential`
    : `${base}/credentials`;
  const raw = await request<unknown>({
    method: "POST",
    path,
    scope: options.scope,
    body: {
      expires_at: options.expiresAt,
      revoke_previous: options.rotate,
    },
    cache: "no-store",
  });
  const envelope = parse(robotIdentityEnvelopeSchema, raw, path);
  if (!envelope.credential) throw new Error("ROBOT_CREDENTIAL_NOT_RETURNED");
  return {
    identity: envelope.data,
    credential: parse(issuedCredentialSchema, envelope.credential, path),
  };
}

async function revokeCredential(options: {
  scope: IngestScope;
  identityId: string;
  credentialId: string;
}) {
  const path = `${identityPath(options.scope, options.identityId)}/credentials/${encodeURIComponent(options.credentialId)}:revoke`;
  const raw = await request<unknown>({
    method: "POST",
    path,
    scope: options.scope,
    cache: "no-store",
  });
  return parse(credentialSummarySchema, raw, path);
}

function identityQueryKey(scope: IngestScope, identityId?: string) {
  return [
    ...queryRoot,
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    identityId ?? "list",
  ] as const;
}

export function useRobotIdentities(scope: IngestScope | null, enabled = true) {
  return useQuery({
    queryKey: scope
      ? identityQueryKey(scope)
      : [...queryRoot, "unscoped", "list"],
    enabled: enabled && scope !== null,
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      if (!scope) throw new Error("INGEST_SCOPE_UNAVAILABLE");
      return listRobotIdentities(scope, signal);
    },
  });
}

export function useRobotIdentityDetail(
  scope: IngestScope | null,
  identityId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey:
      scope && identityId
        ? [...identityQueryKey(scope, identityId), "detail"]
        : [...queryRoot, "unscoped", "detail"],
    enabled: enabled && scope !== null && Boolean(identityId),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      if (!scope || !identityId) throw new Error("ROBOT_IDENTITY_UNAVAILABLE");
      return getRobotIdentity(scope, identityId, signal);
    },
  });
}

export function useRobotCredentialHistory(
  scope: IngestScope | null,
  identityId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey:
      scope && identityId
        ? [...identityQueryKey(scope, identityId), "credentials"]
        : [...queryRoot, "unscoped", "credentials"],
    enabled: enabled && scope !== null && Boolean(identityId),
    staleTime: 5_000,
    queryFn: async ({ signal }) => {
      if (!scope || !identityId) throw new Error("ROBOT_IDENTITY_UNAVAILABLE");
      return listRobotCredentials(scope, identityId, signal);
    },
  });
}

export function useRobotUploadHistory(
  scope: IngestScope | null,
  robotId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: scope
      ? [...identityQueryKey(scope, robotId ?? "none"), "uploads"]
      : [...queryRoot, "unscoped", "uploads"],
    enabled: enabled && scope !== null && Boolean(robotId),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      if (!scope || !robotId) throw new Error("ROBOT_ID_UNAVAILABLE");
      return listRobotUploads(scope, robotId, signal);
    },
  });
}

export function useRobotAttemptHistory(
  scope: IngestScope | null,
  robotId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: scope
      ? [...identityQueryKey(scope, robotId ?? "none"), "attempts"]
      : [...queryRoot, "unscoped", "attempts"],
    enabled: enabled && scope !== null && Boolean(robotId),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      if (!scope || !robotId) throw new Error("ROBOT_ID_UNAVAILABLE");
      return listRobotAttempts(scope, robotId, signal);
    },
  });
}

export function useRobotUploadEpisodes(
  scope: IngestScope | null,
  uploadId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey:
      scope && uploadId
        ? [...identityQueryKey(scope, uploadId), "episodes"]
        : [...queryRoot, "unscoped", "episodes"],
    enabled: enabled && scope !== null && Boolean(uploadId),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      if (!scope || !uploadId) throw new Error("ROBOT_UPLOAD_UNAVAILABLE");
      return listRobotUploadEpisodes(scope, uploadId, signal);
    },
  });
}

export function useRobotStatistics(
  scope: IngestScope | null,
  robotId: string | null,
  enabled = true,
  filters: {
    readonly collectionTaskId?: string;
    readonly sourceFormat?: string;
    readonly createdFrom?: string;
    readonly createdTo?: string;
  } = {},
) {
  return useQuery({
    queryKey: scope
      ? [
          ...identityQueryKey(scope, robotId ?? "none"),
          "statistics",
          filters.collectionTaskId ?? null,
          filters.sourceFormat ?? null,
          filters.createdFrom ?? null,
          filters.createdTo ?? null,
        ]
      : [...queryRoot, "unscoped", "statistics"],
    enabled: enabled && scope !== null && Boolean(robotId),
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      if (!scope || !robotId) throw new Error("ROBOT_ID_UNAVAILABLE");
      return getRobotStatistics(scope, robotId, filters, signal);
    },
  });
}

export function useRobotIdentityMutation(
  operation: "create" | "update" | "enable" | "disable",
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      scope: IngestScope;
      identityId?: string;
      robotId?: string;
      displayName?: string | null;
      allowedFormats?: readonly string[];
      allowedTransports?: readonly string[];
      uploadPolicy?: RobotUploadPolicy;
    }) =>
      mutateIdentity({
        scope: input.scope,
        identityId: input.identityId,
        operation,
        body:
          operation === "create"
            ? {
                robot_id: input.robotId,
                display_name: input.displayName || null,
                allowed_formats: input.allowedFormats,
                allowed_transports: input.allowedTransports ?? ["HTTPS"],
              }
            : operation === "update"
              ? {
                  allowed_formats: input.allowedFormats,
                  allowed_transports: input.allowedTransports,
                  upload_policy: input.uploadPolicy,
                }
              : undefined,
      }),
    onSuccess: (_result, input) => {
      void client.invalidateQueries({ queryKey: queryRoot });
      if (input.identityId) {
        void client.invalidateQueries({
          queryKey: identityQueryKey(input.scope, input.identityId),
        });
      }
    },
  });
}

export function useIssueRobotCredential() {
  const client = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: issueCredential,
    onSuccess: (_result, input) => {
      void client.invalidateQueries({
        queryKey: identityQueryKey(input.scope, input.identityId),
      });
      void client.invalidateQueries({ queryKey: queryRoot });
    },
  });
}

export function useRevokeRobotCredential() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: revokeCredential,
    onSuccess: (_result, input) => {
      void client.invalidateQueries({
        queryKey: identityQueryKey(input.scope, input.identityId),
      });
    },
  });
}

export function replaceIdentityInList(
  identities: readonly RobotIdentity[],
  updated: RobotIdentity,
): readonly RobotIdentity[] {
  return identities.map((identity) =>
    identity.ingest_identity_id === updated.ingest_identity_id
      ? updated
      : identity,
  );
}
