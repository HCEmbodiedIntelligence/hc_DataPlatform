import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AuditSearch } from "../routing";
import type { AuditScope } from "../types";
import {
  getAuditBootstrap,
  getAuditExport,
  getAuditEvent,
  getAuditFacets,
  getAuditIntegrity,
  getAuditRetentionPolicy,
  listAuditLegalHolds,
  listAuditEvents,
  authorizeAuditExportDownload,
  cancelAuditExport,
  createAuditExport,
  createAuditLegalHold,
  releaseAuditLegalHold,
  retryAuditExport,
  updateAuditRetentionPolicy,
} from "./client";
import { auditQueryKeys } from "./query-keys";

export function useAuditBootstrap(
  scope: AuditScope | null,
  search: AuditSearch,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? auditQueryKeys.bootstrap(scope, search)
      : ["audit", "disabled", "bootstrap"],
    queryFn: ({ signal }) => getAuditBootstrap(scope!, search, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    retry: 2,
  });
}

export function useAuditIntegrity(scope: AuditScope | null, enabled: boolean) {
  return useQuery({
    queryKey: scope
      ? auditQueryKeys.integrity(scope)
      : ["audit", "disabled", "integrity"],
    queryFn: ({ signal }) => getAuditIntegrity(scope!, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useAuditFacets(
  scope: AuditScope | null,
  search: AuditSearch,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? auditQueryKeys.facets(scope, search)
      : ["audit", "disabled", "facets"],
    queryFn: ({ signal }) => getAuditFacets(scope!, search, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    retry: 2,
  });
}

export function useAuditEvents(
  scope: AuditScope | null,
  search: AuditSearch,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? auditQueryKeys.events(scope, search)
      : ["audit", "disabled", "events"],
    queryFn: ({ signal }) => listAuditEvents(scope!, search, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    retry: 2,
  });
}

export function useAuditEvent(
  scope: AuditScope | null,
  eventId: string | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey:
      scope && eventId
        ? auditQueryKeys.event(scope, eventId)
        : ["audit", "disabled", "event"],
    queryFn: ({ signal }) => getAuditEvent(scope!, eventId!, signal),
    enabled: enabled && scope !== null && eventId !== undefined,
    staleTime: 300_000,
    retry: false,
  });
}

export function useAuditRetentionPolicy(
  scope: AuditScope | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? auditQueryKeys.retentionPolicy(scope)
      : ["audit", "disabled", "retention-policy"],
    queryFn: ({ signal }) => getAuditRetentionPolicy(scope!, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
  });
}

export function useAuditLegalHolds(scope: AuditScope | null, enabled: boolean) {
  return useQuery({
    queryKey: scope
      ? auditQueryKeys.legalHolds(scope)
      : ["audit", "disabled", "legal-holds"],
    queryFn: ({ signal }) => listAuditLegalHolds(scope!, signal),
    enabled: enabled && scope !== null,
    staleTime: 15_000,
  });
}

export function useAuditExport(
  scope: AuditScope | null,
  jobId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey:
      scope && jobId
        ? auditQueryKeys.export(scope, jobId)
        : ["audit", "disabled", "export"],
    queryFn: ({ signal }) => getAuditExport(scope!, jobId!, signal),
    enabled: enabled && scope !== null && jobId !== null,
    refetchInterval: (query) =>
      query.state.data?.status === "QUEUED" ||
      query.state.data?.status === "RUNNING"
        ? 1_500
        : false,
  });
}

export function useUpdateAuditRetentionPolicy(scope: AuditScope | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      standardDays: number;
      securityDays: number;
      etag: string;
    }) => updateAuditRetentionPolicy(scope!, input),
    onSuccess: (policy) => {
      if (!scope) return;
      queryClient.setQueryData(auditQueryKeys.retentionPolicy(scope), policy);
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

export function useCreateAuditLegalHold(scope: AuditScope | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      reason: string;
      occurredFrom: string;
      occurredTo: string;
    }) => createAuditLegalHold(scope!, input),
    onSuccess: () => {
      if (scope)
        void queryClient.invalidateQueries({
          queryKey: auditQueryKeys.governance(scope),
        });
    },
  });
}

export function useReleaseAuditLegalHold(scope: AuditScope | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (holdId: string) => releaseAuditLegalHold(scope!, holdId),
    onSuccess: () => {
      if (scope)
        void queryClient.invalidateQueries({
          queryKey: auditQueryKeys.governance(scope),
        });
    },
  });
}

export function useCreateAuditExport(scope: AuditScope | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      occurredFrom: string;
      occurredTo: string;
      idempotencyKey: string;
    }) => createAuditExport(scope!, input),
    onSuccess: (job) => {
      if (scope)
        queryClient.setQueryData(auditQueryKeys.export(scope, job.jobId), job);
    },
  });
}

export function useCancelAuditExport(scope: AuditScope | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => cancelAuditExport(scope!, jobId),
    onSuccess: (job) => {
      if (scope)
        queryClient.setQueryData(auditQueryKeys.export(scope, job.jobId), job);
    },
  });
}

export function useRetryAuditExport(scope: AuditScope | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => retryAuditExport(scope!, jobId),
    onSuccess: (job) => {
      if (scope)
        queryClient.setQueryData(auditQueryKeys.export(scope, job.jobId), job);
    },
  });
}

export function useAuthorizeAuditExportDownload(scope: AuditScope | null) {
  return useMutation({
    mutationFn: (jobId: string) => authorizeAuditExportDownload(scope!, jobId),
  });
}
