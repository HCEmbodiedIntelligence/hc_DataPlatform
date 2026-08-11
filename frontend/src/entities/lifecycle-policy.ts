export type DecimalString = `${number}`;
export type UInt64String = `${bigint}`;

export type LifecyclePolicyStatus =
  | 'PENDING_EFFECTIVE'
  | 'ACTIVE'
  | 'PAUSED'
  | 'ERROR'
  | 'UNKNOWN';

export type LifecycleObjectRole =
  | 'SOURCE'
  | 'DERIVED'
  | 'PREVIEW'
  | 'EXPORT'
  | 'INCOMPLETE_MULTIPART'
  | 'UNKNOWN';

export type LifecycleAction =
  | { readonly type: 'TRANSITION'; readonly afterDays: number; readonly targetClass: 'IA' | 'ARCHIVE' }
  | { readonly type: 'DELETE_OBJECT'; readonly afterDays: number }
  | { readonly type: 'ABORT_MULTIPART'; readonly afterDays: number };

export interface BlockedReason {
  readonly code: string;
  readonly message: string;
  readonly blocking: boolean;
  readonly objectCount: UInt64String | null;
  readonly physicalBytes: UInt64String | null;
}

export interface LifecyclePolicy {
  readonly id: string;
  readonly name: string;
  readonly version: UInt64String;
  readonly etag: string;
  readonly status: LifecyclePolicyStatus;
  readonly objectRole: LifecycleObjectRole;
  readonly scopeId: string;
  readonly actions: readonly LifecycleAction[];
  readonly simulationInputHash: string;
  readonly allowedActions: readonly string[];
  readonly blockedReasons: readonly BlockedReason[];
}

