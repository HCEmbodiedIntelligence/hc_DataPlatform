import { useQueryClient } from "@tanstack/react-query";
import { useScopedQuery } from "../../../shared/api/use-scoped-query";
import {
  invalidateScopedDomain,
  invalidateScopedResource,
  scopedMutationKey,
  useScopedMutation,
} from "../../../shared/api/use-scoped-mutation";
import * as api from "./client";

function organizationScope(organizationId: string | null) {
  return organizationId ? { organizationId } : null;
}

export function useScopedRobotModels(
  organizationId: string | null,
  filters: Readonly<Record<string, unknown>> = {},
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robot-models",
    "list",
    filters,
    {
      enabled: Boolean(organizationId),
      staleTime: 30_000,
      queryFn: ({ signal }) =>
        api.listRobotModels(organizationId!, filters, signal),
    },
  );
}

export function useScopedRobotModelVersion(
  organizationId: string | null,
  versionId: string | null,
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robot-models",
    "version",
    versionId,
    {
      enabled: Boolean(organizationId && versionId),
      queryFn: ({ signal }) =>
        api.getRobotModelVersion(organizationId!, versionId!, signal),
    },
  );
}

export function useScopedRobotModelAssets(
  organizationId: string | null,
  versionId: string | null,
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robot-models",
    "assets",
    versionId,
    {
      enabled: Boolean(organizationId && versionId),
      staleTime: 30_000,
      queryFn: ({ signal }) =>
        api.listRobotModelAssets(organizationId!, versionId!, signal),
    },
  );
}

export function useScopedRobotModelJointMappings(
  organizationId: string | null,
  versionId: string | null,
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robot-models",
    "joint-mappings",
    versionId,
    {
      enabled: Boolean(organizationId && versionId),
      staleTime: 30_000,
      queryFn: ({ signal }) =>
        api.listRobotModelJointMappings(organizationId!, versionId!, signal),
    },
  );
}

export function useScopedRobotModelBindings(
  organizationId: string | null,
  versionId: string | null,
) {
  return useScopedQuery(
    organizationScope(organizationId),
    "robot-models",
    "bindings",
    versionId,
    {
      enabled: Boolean(organizationId && versionId),
      staleTime: 30_000,
      queryFn: ({ signal }) =>
        api.listRobotModelBindings(organizationId!, versionId!, signal),
    },
  );
}

function useCreateVersion<
  T extends api.CreateRobotModelIntent | api.CreateRobotModelDraftIntent,
>(
  organizationId: string | null,
  operation: (
    organizationId: string,
    intent: T,
  ) => ReturnType<typeof api.createRobotModel>,
) {
  const client = useQueryClient();
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: T) => operation(scope.organizationId, intent),
    {
      onSuccess: (version, _intent, binding) => {
        client.setQueryData(
          scopedMutationKey(binding, "robot-models", "version", version.id),
          version,
        );
        void invalidateScopedDomain(client, binding, "robot-models");
      },
    },
  );
}

export function useScopedCreateRobotModel(organizationId: string | null) {
  return useCreateVersion(organizationId, api.createRobotModel);
}
export function useScopedCreateRobotModelDraft(organizationId: string | null) {
  return useCreateVersion(organizationId, api.createRobotModelDraft);
}

export function useScopedRobotModelAssetDownload(
  organizationId: string | null,
) {
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: Readonly<{ versionId: string; assetId: string }>) =>
      api.authorizeRobotModelAssetDownload(
        scope.organizationId,
        intent.versionId,
        intent.assetId,
      ),
    { gcTime: 0 },
  );
}

export function useScopedUploadRobotModelAssets(organizationId: string | null) {
  const client = useQueryClient();
  return useScopedMutation(
    organizationScope(organizationId),
    (
      scope,
      intent: Readonly<{
        versionId: string;
        files: readonly api.RobotModelAssetUploadInput[];
        idempotencyKey: string;
      }>,
    ) =>
      api.uploadRobotModelAssets(
        scope.organizationId,
        intent.versionId,
        intent.files,
        intent.idempotencyKey,
      ),
    {
      onSuccess: (_uploadId, intent, binding) => {
        void invalidateScopedResource(
          client,
          binding,
          "robot-models",
          "assets",
          intent.versionId,
        );
        void invalidateScopedResource(
          client,
          binding,
          "robot-models",
          "version",
          intent.versionId,
        );
      },
      gcTime: 0,
    },
  );
}

export function useScopedLoadRobotBindingTarget(organizationId: string | null) {
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, target: api.RobotBindingTarget) =>
      api.loadRobotBindingTarget(scope.organizationId, target),
    { gcTime: 0 },
  );
}

export function useScopedBindRobotModelVersion(organizationId: string | null) {
  const client = useQueryClient();
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: api.BindRobotModelVersionIntent) =>
      api.bindRobotModelVersion(scope.organizationId, intent),
    {
      onSuccess: (_binding, intent, binding) => {
        void invalidateScopedResource(
          client,
          binding,
          "robot-models",
          "bindings",
          intent.versionId,
        );
        void invalidateScopedDomain(client, binding, "robots");
      },
      gcTime: 0,
    },
  );
}

export function useScopedReplaceRobotModelJointMappings(
  organizationId: string | null,
) {
  const client = useQueryClient();
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: api.ReplaceRobotModelJointMappingsIntent) =>
      api.replaceRobotModelJointMappings(scope.organizationId, intent),
    {
      onSuccess: (version, intent, binding) => {
        client.setQueryData(
          scopedMutationKey(
            binding,
            "robot-models",
            "version",
            intent.versionId,
          ),
          version,
        );
        void invalidateScopedResource(
          client,
          binding,
          "robot-models",
          "joint-mappings",
          intent.versionId,
        );
      },
      gcTime: 0,
    },
  );
}

export function useScopedPreflightRobotModelPublish(
  organizationId: string | null,
) {
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: api.RobotModelPublishPreflightIntent) =>
      api.preflightRobotModelPublish(scope.organizationId, intent),
    { gcTime: 0 },
  );
}

export function useScopedPublishRobotModelVersion(
  organizationId: string | null,
) {
  const client = useQueryClient();
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: api.RobotModelDangerousIntent) =>
      api.publishRobotModelVersion(scope.organizationId, intent),
    {
      onSuccess: (version, intent, binding) => {
        client.setQueryData(
          scopedMutationKey(
            binding,
            "robot-models",
            "version",
            intent.versionId,
          ),
          version,
        );
        void invalidateScopedDomain(client, binding, "robot-models");
      },
    },
  );
}

export function useScopedCreateRobotAssetUploadSession(
  organizationId: string | null,
) {
  return useScopedMutation(
    organizationScope(organizationId),
    (scope, intent: api.UploadSessionIntent) =>
      api.createRobotAssetUploadSession(scope.organizationId, intent),
    { gcTime: 0 },
  );
}
