export type Brand<T, Name extends string> = T & { readonly __brand: Name };

export type DataSourceId = Brand<string, 'DataSourceId'>;
export type DataSourceETag = Brand<string, 'DataSourceETag'>;
export type DecimalString = Brand<string, 'DecimalString'>;

export interface UnknownEnum {
  readonly kind: 'UNKNOWN';
  readonly raw: string;
}

export interface IngestScope {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
}

export interface BlockedReason {
  readonly code: string;
  readonly message: string;
}

export type AdministrativeState = 'ENABLED' | 'DISABLED' | UnknownEnum;
export type CredentialState =
  | 'NOT_REQUIRED'
  | 'MISSING'
  | 'CONFIGURED'
  | 'ROTATION_DUE'
  | 'EXPIRED'
  | 'REVOKED'
  | 'INVALID'
  | UnknownEnum;
export type ConnectivityState =
  | 'UNKNOWN'
  | 'ONLINE'
  | 'DEGRADED'
  | 'OFFLINE'
  | 'AUTH_FAILED'
  | 'CONFIG_ERROR'
  | UnknownEnum;

export type DataSourceAllowedAction =
  | 'VIEW'
  | 'EDIT_CONFIGURATION'
  | 'ROTATE_CREDENTIAL'
  | 'TEST_CONNECTION'
  | 'ENABLE'
  | 'DISABLE'
  | 'DELETE'
  | 'OPEN_UPLOADS';

export interface CredentialSummary {
  readonly kind: string;
  readonly state: CredentialState;
  readonly configured: boolean;
  readonly maskedHint: string | null;
  readonly version: DecimalString;
  readonly updatedAt: string | null;
  readonly expiresAt: string | null;
  readonly rotationDueAt: string | null;
}

export interface ConnectivitySummary {
  readonly state: ConnectivityState;
  readonly lastCheckState: string;
  readonly observedConfigVersion: DecimalString | null;
  readonly observedCredentialVersion: DecimalString | null;
  readonly checkedAt: string | null;
  readonly safeError: { readonly code: string; readonly message: string } | null;
}

export interface DataSourceSummary {
  readonly id: DataSourceId;
  readonly scope: IngestScope;
  readonly name: string;
  readonly sourceType: string | UnknownEnum;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly adapterVersion: string | null;
  readonly binding:
    | { readonly kind: 'ROBOT'; readonly robotId: string; readonly displayName: string | null }
    | { readonly kind: 'EDGE_AGENT'; readonly agentId: string; readonly displayName: string | null }
    | { readonly kind: 'OSS_IMPORT'; readonly sourceAlias: string; readonly displayName: string | null }
    | { readonly kind: 'UNKNOWN'; readonly rawSourceType: string; readonly safeDisplayName: string | null };
  readonly administrativeState: AdministrativeState;
  readonly credential: CredentialSummary;
  readonly connectivity: ConnectivitySummary;
  readonly heartbeat: { readonly state: string; readonly lastSeenAt: string | null } | null;
  readonly uploadPolicy: {
    readonly code: string;
    readonly label: string;
    readonly maxObjectSizeBytes: DecimalString;
  };
  readonly lastUpload: {
    readonly uploadId: string;
    readonly completedAt: string;
    readonly verifiedBytes: DecimalString;
    readonly lifecycleStatus: string;
  } | null;
  readonly configVersion: DecimalString;
  readonly credentialVersion: DecimalString;
  readonly etag: DataSourceETag;
  readonly allowedActions: readonly DataSourceAllowedAction[];
  readonly blockedReasons: readonly BlockedReason[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DataSourceDetail<TConfiguration = unknown> extends DataSourceSummary {
  readonly configuration: TConfiguration;
}

export function isUnknownEnum(value: unknown): value is UnknownEnum {
  return typeof value === 'object' && value !== null && (value as UnknownEnum).kind === 'UNKNOWN';
}

export function canMutateDataSource(source: DataSourceSummary): boolean {
  return !isUnknownEnum(source.sourceType) && !isUnknownEnum(source.administrativeState);
}
