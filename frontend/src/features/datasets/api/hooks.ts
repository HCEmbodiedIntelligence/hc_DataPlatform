import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DatasetId } from "../../../entities/dataset";
import type { DatasetVersionId } from "../../../entities/dataset-version";
import type { EpisodeId, EpisodeRevisionId } from "../../../entities/episode";
import { makeQueryKey, normalizeFilters } from "../../../shared/api/query-keys";
import { useShellStore } from "../../../shared/scope/shell-store";
import type {
  ApproveReviewCommandWire,
  CreateDatasetRequestWire,
  ReturnReviewCommandWire,
} from "./wire-schemas";
import {
  approveVersionReview,
  createDataset,
  createVersionDiffJob,
  fetchDatasetBootstrap,
  fetchDatasetFacets,
  fetchDatasets,
  fetchDatasetsPageCapabilities,
  fetchDatasetSummary,
  fetchDatasetVersionCapacity,
  fetchDatasetVersionSchemaSummary,
  fetchDatasetVersionSourceProvenance,
  fetchDatasetVersions,
  fetchVersionBootstrap,
  fetchEpisodeRevisionHistory,
  fetchEpisodeRevision,
  fetchOperationalInventory,
  fetchRequiredStorage,
  fetchVersionEpisodes,
  fetchVersionManifest,
  fetchVersionSchema,
  preflightDeletion,
  publishDatasetVersion,
  resolveViewerEpisode,
  returnVersionReview,
  runReviewChecks,
  type DatasetListApiFilters,
  type EpisodeListApiFilters,
  type EpisodeRevisionHistoryApiFilters,
  type VersionListApiFilters,
  type PublishDatasetVersionCommand,
} from "./queries";

function useScopedKey(
  domain: string,
  resource: string,
  identity: unknown,
  revision?: string,
) {
  const scopeKey = useShellStore((state) => state.scopeKey);
  return useMemo(() => {
    void scopeKey;
    return makeQueryKey(domain, resource, identity, revision);
  }, [domain, identity, resource, revision, scopeKey]);
}

export function useDatasetsQuery(
  filters: DatasetListApiFilters,
  enabled = true,
) {
  const normalized = normalizeFilters(filters, {
    sort: "activityDesc",
    limit: 20,
  });
  const key = useScopedKey("datasets", "list", normalized);
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchDatasets(filters, signal),
    enabled,
    staleTime: 30_000,
  });
}

export function useDatasetsPageCapabilitiesQuery(enabled = true) {
  const key = useScopedKey("datasets", "page-capabilities", {});
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchDatasetsPageCapabilities(signal),
    enabled,
  });
}

export function useDatasetSummaryQuery(
  filters: DatasetListApiFilters,
  enabled = true,
) {
  const normalized = normalizeFilters(filters, {
    sort: "activityDesc",
    limit: 20,
  });
  const key = useScopedKey("datasets", "summary", normalized);
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchDatasetSummary(filters, signal),
    enabled,
    staleTime: 30_000,
  });
}

export function useDatasetFacetsQuery(
  filters: DatasetListApiFilters,
  enabled = true,
) {
  const normalized = normalizeFilters(filters, {
    sort: "activityDesc",
    limit: 20,
  });
  const key = useScopedKey("datasets", "facets", normalized);
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchDatasetFacets(filters, signal),
    enabled,
    staleTime: 300_000,
  });
}

export function useDatasetBootstrapQuery(datasetId: DatasetId, enabled = true) {
  const key = useScopedKey("datasets", "bootstrap", { datasetId });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchDatasetBootstrap(datasetId, signal),
    enabled,
  });
}

export function useDatasetVersionsQuery(
  datasetId: DatasetId,
  filters: VersionListApiFilters,
  enabled = true,
) {
  const key = useScopedKey("datasets", "versions", {
    datasetId,
    filters: normalizeFilters(filters, { sort: "created-desc", limit: 20 }),
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchDatasetVersions(datasetId, filters, signal),
    enabled,
  });
}

export function usePublishDatasetVersionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: PublishDatasetVersionCommand) =>
      publishDatasetVersion(command),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function useDatasetVersionSchemaSummaryQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  enabled = true,
) {
  const key = useScopedKey("datasets", "schema-summary", {
    datasetId,
    versionId,
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchDatasetVersionSchemaSummary(datasetId, versionId, signal),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useDatasetVersionSourceProvenanceQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  filters: Readonly<{
    q?: string;
    sourceId?: string;
    sort?: string;
    after?: string;
    before?: string;
    limit?: 10 | 20 | 50;
  }>,
  enabled = true,
) {
  const key = useScopedKey("datasets", "source-provenance", {
    datasetId,
    versionId,
    filters,
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchDatasetVersionSourceProvenance(
        datasetId,
        versionId,
        filters,
        signal,
      ),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useDatasetVersionCapacityQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  enabled = true,
) {
  const key = useScopedKey("datasets", "capacity-facts", {
    datasetId,
    versionId,
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchDatasetVersionCapacity(datasetId, versionId, signal),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.state === "CALCULATING" ? 5_000 : false,
  });
}

export function useVersionEpisodesQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  filters: EpisodeListApiFilters,
  enabled = true,
) {
  const key = useScopedKey(
    "datasets",
    "episodes",
    {
      datasetId,
      versionId,
      filters: normalizeFilters(filters, { sort: "ordinal-asc", limit: 20 }),
    },
    filters.snapshotToken,
  );
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchVersionEpisodes(datasetId, versionId, filters, signal),
    enabled,
  });
}

export function useVersionBootstrapQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  enabled = true,
) {
  const key = useScopedKey("datasets", "version-bootstrap", {
    datasetId,
    versionId,
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchVersionBootstrap(datasetId, versionId, signal),
    enabled,
  });
}

export function useVersionManifestQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  filters: { after?: string; before?: string; limit?: number },
  enabled = true,
) {
  const key = useScopedKey("datasets", "manifest", {
    datasetId,
    versionId,
    filters,
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchVersionManifest(datasetId, versionId, filters, signal),
    enabled,
  });
}

export function useEpisodeRevisionQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  revisionId: EpisodeRevisionId | undefined,
  snapshotToken: string | undefined,
  enabled = true,
) {
  const key = useScopedKey(
    "datasets",
    "episode-revision",
    { datasetId, versionId, revisionId },
    snapshotToken,
  );
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchEpisodeRevision(
        datasetId,
        versionId,
        revisionId!,
        snapshotToken!,
        signal,
      ),
    enabled: enabled && Boolean(revisionId) && Boolean(snapshotToken),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useEpisodeRevisionHistoryQuery(
  datasetId: DatasetId,
  episodeId: EpisodeId | undefined,
  filters: EpisodeRevisionHistoryApiFilters,
  enabled = true,
) {
  const key = useScopedKey("datasets", "episode-revision-history", {
    datasetId,
    episodeId,
    filters: normalizeFilters(filters, { limit: 20 }),
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchEpisodeRevisionHistory(datasetId, episodeId!, filters, signal),
    enabled: enabled && Boolean(episodeId),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useVersionSchemaQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  snapshotToken: string | undefined,
  enabled = true,
) {
  const key = useScopedKey(
    "datasets",
    "version-schema",
    { datasetId, versionId },
    snapshotToken,
  );
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchVersionSchema(datasetId, versionId, snapshotToken!, signal),
    enabled: enabled && Boolean(snapshotToken),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useRequiredStorageQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  snapshotToken: string | undefined,
  filters: { after?: string; before?: string; limit?: 20 | 50 | 100 },
  enabled = true,
) {
  const key = useScopedKey(
    "datasets",
    "required-storage",
    { datasetId, versionId, filters },
    snapshotToken,
  );
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchRequiredStorage(
        datasetId,
        versionId,
        snapshotToken!,
        filters,
        signal,
      ),
    enabled: enabled && Boolean(snapshotToken),
  });
}

export function useOperationalInventoryQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  operationalRevision: string | undefined,
  filters: { after?: string; before?: string; limit?: 20 | 50 | 100 },
  enabled = true,
) {
  const key = useScopedKey(
    "datasets",
    "operational-inventory",
    { datasetId, versionId, filters },
    operationalRevision,
  );
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchOperationalInventory(
        datasetId,
        versionId,
        operationalRevision!,
        filters,
        signal,
      ),
    enabled: enabled && Boolean(operationalRevision),
    staleTime: 15_000,
  });
}

export function useViewerEpisodeQuery(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  episodeId: EpisodeId,
  enabled = true,
) {
  const key = useScopedKey("datasets", "episode-viewer", {
    datasetId,
    versionId,
    episodeId,
  });
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      resolveViewerEpisode(datasetId, versionId, episodeId, signal),
    enabled,
  });
}

export function useCreateDatasetMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      command: CreateDatasetRequestWire;
      idempotencyKey: string;
    }) => createDataset(input.command, input.idempotencyKey),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function useReviewChecksMutation() {
  return useMutation({
    mutationFn: (input: {
      datasetId: DatasetId;
      versionId: DatasetVersionId;
      etag: string;
    }) => runReviewChecks(input.datasetId, input.versionId, input.etag),
  });
}

export function useApproveReviewMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      datasetId: DatasetId;
      versionId: DatasetVersionId;
      etag: string;
      idempotencyKey: string;
      command: ApproveReviewCommandWire;
    }) =>
      approveVersionReview(
        input.datasetId,
        input.versionId,
        input.command,
        input.etag,
        input.idempotencyKey,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function useReturnReviewMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      datasetId: DatasetId;
      versionId: DatasetVersionId;
      expectedSourceDraftId: string;
      etag: string;
      idempotencyKey: string;
      command: ReturnReviewCommandWire;
    }) =>
      returnVersionReview(
        input.datasetId,
        input.versionId,
        input.command,
        input.expectedSourceDraftId,
        input.etag,
        input.idempotencyKey,
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function useDeletionPreflightMutation() {
  return useMutation({ mutationFn: preflightDeletion });
}

export function useCreateVersionDiffJobMutation() {
  return useMutation({ mutationFn: createVersionDiffJob });
}
