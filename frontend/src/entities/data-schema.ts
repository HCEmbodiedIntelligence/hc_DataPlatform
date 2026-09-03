export type DataSchemaStatus = 'DRAFT' | 'VALIDATING' | 'PUBLISHED' | 'UNKNOWN';
export type CompatibilityMode = 'STRICT' | 'BACKWARD' | 'FORWARD' | 'FULL' | 'MANUAL' | 'UNKNOWN';
export type CompatibilityResult = 'BACKWARD' | 'FORWARD' | 'FULL' | 'NONE' | 'UNKNOWN';

export interface SchemaHash {
  readonly algorithm: 'SHA-256' | 'UNKNOWN';
  readonly canonicalizationVersion: string;
  readonly value: string;
}

export interface DataSchemaVersion {
  readonly schemaId: string;
  readonly familyId: string;
  readonly version: `${bigint}`;
  readonly displayName: string;
  readonly logicalType: string;
  readonly status: DataSchemaStatus;
  readonly compatibilityMode: CompatibilityMode;
  readonly compatibilityResult: CompatibilityResult | null;
  readonly hash: SchemaHash | null;
  readonly definition: Readonly<Record<string, unknown>>;
  readonly etag: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly { code: string; message: string }[];
}

