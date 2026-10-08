import type {
  RobotModel,
  RobotModelVersion,
} from "../../../entities/robot-model";
import type {
  CreateRobotModelIntent,
  CreateRobotModelDraftIntent,
  RobotModelAssetUploadInput,
  RobotModelDangerousIntent,
  RobotModelJointMapping,
  RobotModelPublishPreflight,
  RobotModelPublishPreflightIntent,
  ReplaceRobotModelJointMappingsIntent,
} from "../api/client";
import type { PendingRobotModelAsset } from "./model-file-selection";
import type { ParsedRobotModel } from "./urdf-import";

export type ImportStage = "files" | "review" | "saving" | "saved";

export interface ModelMetadataFormValues {
  readonly manufacturer?: string;
  readonly modelCode?: string;
  readonly displayName?: string;
  readonly versionLabel: string;
}

/** One bound organization/session operation implements these independent domain commands. */
export interface RobotModelImportGateway {
  createModel(intent: CreateRobotModelIntent): Promise<RobotModelVersion>;
  createDraft(intent: CreateRobotModelDraftIntent): Promise<RobotModelVersion>;
  uploadAssets(
    versionId: string,
    files: readonly RobotModelAssetUploadInput[],
    key: string,
  ): Promise<string>;
  getVersion(versionId: string): Promise<RobotModelVersion>;
  replaceMappings(
    intent: ReplaceRobotModelJointMappingsIntent,
  ): Promise<RobotModelVersion>;
  preflight(
    intent: RobotModelPublishPreflightIntent,
  ): Promise<RobotModelPublishPreflight>;
  publish(intent: RobotModelDangerousIntent): Promise<RobotModelVersion>;
  discard(versionId: string, fallbackVersionId?: string): Promise<void>;
}

export interface RobotModelImportInput {
  readonly analysis: ParsedRobotModel;
  readonly assets: readonly PendingRobotModelAsset[];
  readonly mappings: readonly RobotModelJointMapping[];
  readonly metadata: ModelMetadataFormValues;
  readonly model: RobotModel | null;
  readonly sourceVersion: RobotModelVersion | null;
  readonly commandId: string;
}
