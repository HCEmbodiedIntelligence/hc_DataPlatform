import type { ProjectRoleId } from "./actor";
import type { ScopeKey } from "./scope";

export const CANONICAL_CAPABILITIES = [
  "access.manage",
  "access.read",
  "annotation.edit",
  "annotation.review",
  "annotation.save",
  "annotation.submit",
  "annotation_draft.edit",
  "annotation_set.read",
  "annotation_task.assign",
  "annotation_task.claim",
  "annotation_task.create",
  "annotation_task.read",
  "annotation_task.rebase",
  "audit.export",
  "audit.read",
  "calibration.create",
  "calibration.publish",
  "calibration.read",
  "calibration.validate",
  "cleaning.create",
  "cleaning.edit",
  "cleaning.preview",
  "cleaning.read",
  "cleaning.submit",
  "data_schema.create",
  "data_schema.import",
  "data_schema.publish",
  "data_schema.read",
  "data_schema.validate",
  "dataset.create",
  "dataset.read",
  "dataset_version.download_manifest",
  "dataset_version.download_raw",
  "dataset_version.publish",
  "dataset_version.read",
  "dataset_version.review",
  "episode.read",
  "export.create",
  "export.download",
  "export.read",
  "ingest.import",
  "ingest_source.manage",
  "ingest_source.read",
  "manual_issue.create",
  "manual_issue.read",
  "manual_issue.resolve",
  "manual_issue.triage",
  "robot.create",
  "robot.disable",
  "robot.read",
  "robot.update",
  "robot_component.change_mount",
  "robot_component.create",
  "robot_component.disable",
  "robot_component.update",
  "robot_model.create",
  "robot_model.disable",
  "robot_model.publish",
  "robot_model.read",
  "robot_model.validate",
  "robot_model_binding.manage",
  "robot_model_binding.read",
  "storage.cost.read",
  "storage.inventory.refresh",
  "storage.lifecycle.execute",
  "storage.lifecycle.manage",
  "storage.lifecycle.approve",
  "storage.lifecycle.read",
  "storage.lifecycle.simulate",
  "storage.multipart.abort",
  "storage.multipart.read",
  "storage.object.read",
  "storage.object.manage",
  "storage.overview.read",
  "storage.restore.read",
  "storage.restore.request",
  "upload.manage",
  "upload.read",
] as const;

export type Capability = (typeof CANONICAL_CAPABILITIES)[number];

const capabilitySet: ReadonlySet<string> = new Set(CANONICAL_CAPABILITIES);

export function isCapability(value: unknown): value is Capability {
  return typeof value === "string" && capabilitySet.has(value);
}

export const DEVELOPER_CAPABILITIES = [
  "annotation.edit",
  "annotation.review",
  "annotation.save",
  "annotation.submit",
  "annotation_draft.edit",
  "annotation_set.read",
  "annotation_task.assign",
  "annotation_task.claim",
  "annotation_task.create",
  "annotation_task.read",
  "annotation_task.rebase",
  "cleaning.create",
  "cleaning.edit",
  "cleaning.preview",
  "cleaning.read",
  "cleaning.submit",
  "dataset.create",
  "dataset.read",
  "dataset_version.download_manifest",
  "dataset_version.download_raw",
  "dataset_version.read",
  "episode.read",
  "export.create",
  "export.download",
  "export.read",
  "ingest.import",
  "ingest_source.manage",
  "ingest_source.read",
  "manual_issue.create",
  "manual_issue.read",
  "manual_issue.resolve",
  "manual_issue.triage",
  "upload.manage",
  "upload.read",
] as const;

export const DATA_PROCESSOR_CAPABILITIES = [
  "annotation.edit",
  "annotation.save",
  "annotation.submit",
  "annotation_draft.edit",
  "annotation_set.read",
  "annotation_task.claim",
  "annotation_task.read",
  "cleaning.create",
  "cleaning.edit",
  "cleaning.preview",
  "cleaning.read",
  "cleaning.submit",
  "dataset.read",
  "dataset_version.read",
  "episode.read",
  "manual_issue.create",
  "manual_issue.read",
  "manual_issue.resolve",
  "manual_issue.triage",
] as const;

export const PROJECT_ROLE_CAPABILITIES: Readonly<
  Record<ProjectRoleId, readonly Capability[]>
> = Object.freeze({
  PROJECT_ADMIN: CANONICAL_CAPABILITIES,
  PROJECT_DEVELOPER: DEVELOPER_CAPABILITIES,
  PROJECT_DATA_PROCESSOR: DATA_PROCESSOR_CAPABILITIES,
});

export interface AuthorizationSnapshot {
  scopeKey: ScopeKey;
  roleVersion: string;
  /** Exact capability keys returned by the runtime bootstrap. */
  capabilities: readonly string[];
  fetchedAt: string;
  expiresAt?: string;
}
