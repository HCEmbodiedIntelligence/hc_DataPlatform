export interface Component {
  readonly id: string;
  readonly robotId: string;
  readonly parentComponentId: string | null;
  readonly componentModelId: string;
  readonly componentType: string;
  readonly displayName: string;
  readonly serialNo: string;
  readonly sortOrder: `${bigint}`;
  readonly lifecycle: 'DRAFT' | 'ACTIVE' | 'MAINTENANCE' | 'DISABLED' | 'RETIRED' | 'UNKNOWN';
}

export interface EffectiveRelation {
  readonly id: string;
  readonly componentId: string;
  readonly relationType: 'ROBOT' | 'PARENT' | 'FRAME' | 'CHANNEL' | 'SCHEMA' | 'CALIBRATION';
  readonly targetId: string;
  readonly validFrom: string;
  readonly validTo: string | null;
}

export interface ComponentTreeNode extends Component {
  readonly children: readonly ComponentTreeNode[];
}

