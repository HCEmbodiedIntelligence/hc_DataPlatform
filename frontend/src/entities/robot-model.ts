export type RobotModelLifecycle = 'DRAFT' | 'PUBLISHED' | 'DISABLED' | 'UNKNOWN';
export type AssetAvailability = 'UNKNOWN' | 'AVAILABLE' | 'PARTIAL' | 'MISSING';
export type PublishReadiness =
  | 'CONFIGURATION_REQUIRED'
  | 'MAPPING_REQUIRED'
  | 'SAMPLE_VALIDATION_REQUIRED'
  | 'READY'
  | 'BLOCKED'
  | 'UNKNOWN';

export interface RobotModel {
  readonly id: string;
  readonly manufacturer: string;
  readonly modelCode: string;
  readonly displayName: string;
  readonly currentPublishedVersionId: string | null;
}

export interface RobotAssetObject {
  readonly relativePath: string;
  readonly role: string;
  readonly mediaType: string;
  readonly bytes: `${bigint}`;
  readonly sha256: string;
  readonly multipartEtag: string | null;
}

export interface JointMapping {
  readonly dataJoint: string;
  readonly urdfJoint: string;
  readonly direction: 1 | -1;
  readonly scale: string;
  readonly offset: string;
  readonly required: boolean;
  readonly status: 'MAPPED' | 'MISSING' | 'INCOMPATIBLE' | 'UNKNOWN';
}

export interface RobotModelVersion {
  readonly id: string;
  readonly robotModelId: string;
  readonly versionLabel: string;
  readonly lifecycle: RobotModelLifecycle;
  readonly assetAvailability: AssetAvailability;
  readonly publishReadiness: PublishReadiness;
  readonly assetManifestHash: string | null;
  readonly validationInputHash: string | null;
  readonly etag: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly { code: string; message: string }[];
}

