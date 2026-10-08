/** Compatibility session UI entry point. Reusable workspaces use explicit hooks.ts and client.ts. */
import {
  getCurrentOrganizationId,
  useCurrentOrganizationId,
} from "../../../shared/scope/current-organization";
import * as hooks from "./hooks";
import {
  loadRobotBindingTarget as loadScopedRobotBindingTarget,
  type RobotBindingTarget,
} from "./client";
export * from "./client";
export * from "./hooks";

/** Legacy selection adapter, retained for callers transitioning to the explicit client. */
export function loadRobotBindingTarget(target: RobotBindingTarget) {
  return loadScopedRobotBindingTarget(getCurrentOrganizationId(), target);
}
export function useRobotModels(
  filters: Readonly<Record<string, unknown>> = {},
) {
  return hooks.useScopedRobotModels(useCurrentOrganizationId(), filters);
}
export function useRobotModelVersion(versionId: string | null) {
  return hooks.useScopedRobotModelVersion(
    useCurrentOrganizationId(),
    versionId,
  );
}
export function useRobotModelAssets(versionId: string | null) {
  return hooks.useScopedRobotModelAssets(useCurrentOrganizationId(), versionId);
}
export function useRobotModelJointMappings(versionId: string | null) {
  return hooks.useScopedRobotModelJointMappings(
    useCurrentOrganizationId(),
    versionId,
  );
}
export function useRobotModelBindings(versionId: string | null) {
  return hooks.useScopedRobotModelBindings(
    useCurrentOrganizationId(),
    versionId,
  );
}
export function useCreateRobotModel() {
  return hooks.useScopedCreateRobotModel(useCurrentOrganizationId());
}
export function useCreateRobotModelDraft() {
  return hooks.useScopedCreateRobotModelDraft(useCurrentOrganizationId());
}
export function useRobotModelAssetDownload() {
  return hooks.useScopedRobotModelAssetDownload(useCurrentOrganizationId());
}
export function useUploadRobotModelAssets() {
  return hooks.useScopedUploadRobotModelAssets(useCurrentOrganizationId());
}
export function useLoadRobotBindingTarget() {
  return hooks.useScopedLoadRobotBindingTarget(useCurrentOrganizationId());
}
export function useBindRobotModelVersion() {
  return hooks.useScopedBindRobotModelVersion(useCurrentOrganizationId());
}
export function useReplaceRobotModelJointMappings() {
  return hooks.useScopedReplaceRobotModelJointMappings(
    useCurrentOrganizationId(),
  );
}
export function usePreflightRobotModelPublish() {
  return hooks.useScopedPreflightRobotModelPublish(useCurrentOrganizationId());
}
export function usePublishRobotModelVersion() {
  return hooks.useScopedPublishRobotModelVersion(useCurrentOrganizationId());
}
export function useCreateRobotAssetUploadSession() {
  return hooks.useScopedCreateRobotAssetUploadSession(
    useCurrentOrganizationId(),
  );
}
