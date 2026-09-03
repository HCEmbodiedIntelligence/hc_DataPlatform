/** Target-owner builders are wired here only after their public contracts land. */
export const pendingAuditResourceLinks = {
  DATASET: null,
  DATASET_VERSION: null,
  EPISODE: null,
  MANUAL_ISSUE: null,
  CLEANING_DRAFT: null,
  ROBOT: null,
  ROBOT_MODEL_VERSION: null,
  CALIBRATION_SET: null,
  DATA_SCHEMA: null,
  PROJECT_MEMBERSHIP: null,
  RETIRED_RESOURCE: null,
} as const;

export function resolvePendingAuditResourceLink(resourceType: string): null {
  void resourceType;
  return null;
}
