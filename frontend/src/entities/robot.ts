export type RobotLifecycle = 'DRAFT' | 'ACTIVE' | 'MAINTENANCE' | 'DISABLED' | 'RETIRED' | 'UNKNOWN';
export type ConnectivityState = 'ONLINE' | 'OFFLINE' | 'DEGRADED' | 'UNKNOWN';

export interface Connectivity {
  readonly state: ConnectivityState;
  readonly observedAt: string | null;
  readonly source: string | null;
  readonly reasonCode: string | null;
}

export interface EffectiveModelBinding {
  readonly id: string;
  readonly scopeType: 'ROBOT_MODEL_DEFAULT' | 'ROBOT_INSTANCE';
  readonly scopeId: string;
  readonly robotModelVersionId: string;
  readonly validFrom: string;
  readonly validTo: string | null;
  readonly etag: string;
}

export interface Robot {
  readonly id: string;
  readonly displayName: string;
  readonly serialNo: string;
  readonly lifecycle: RobotLifecycle;
  readonly connectivity: Connectivity;
  readonly effectiveModelBinding: EffectiveModelBinding | null;
  readonly etag: string;
  readonly topologyRevision: string;
  readonly allowedActions: readonly string[];
}

