import type { ProjectRoleId } from "../shared/auth/contracts";
import {
  CANONICAL_CAPABILITIES,
  type Capability,
} from "../shared/auth/capabilities";

export {
  CANONICAL_CAPABILITIES,
  isCapability,
} from "../shared/auth/capabilities";
export type { Capability } from "../shared/auth/capabilities";
export type { AuthorizationSnapshot } from "../shared/auth/contracts";

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
