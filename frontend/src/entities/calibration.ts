export type PrecisionDecimal = string;
export type CalibrationSnapshotStatus = 'DRAFT' | 'READY' | 'UNKNOWN';
export type CalibrationAvailability = 'SCHEDULED' | 'ACTIVE' | 'EXPIRED' | 'REVOKED' | 'UNKNOWN';

export interface Transform {
  readonly parentFrame: string;
  readonly childFrame: string;
  readonly translation: readonly [PrecisionDecimal, PrecisionDecimal, PrecisionDecimal];
  readonly quaternionXyzw: readonly [PrecisionDecimal, PrecisionDecimal, PrecisionDecimal, PrecisionDecimal];
}

export interface Covariance {
  readonly dimension: 6;
  readonly values: readonly PrecisionDecimal[];
}

export interface CalibrationSet {
  readonly id: string;
  readonly robotId: string;
  readonly componentId: string | null;
  readonly version: `${bigint}`;
  readonly snapshotStatus: CalibrationSnapshotStatus;
  readonly availability: CalibrationAvailability | null;
  readonly contentHash: string | null;
  readonly validationContextHash: string | null;
  readonly validation: {
    readonly status: 'PASSED' | 'FAILED' | 'STALE' | 'UNKNOWN';
    readonly contentHash: string;
    readonly validationContextHash: string;
    readonly reportId: string;
  } | null;
  readonly etag: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly { code: string; message: string }[];
}
