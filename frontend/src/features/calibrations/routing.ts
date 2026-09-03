export type CalibrationRouteParams = {
  readonly robotId: string;
  readonly componentId: string;
  readonly setId: string;
};

export type CalibrationRouteResolution =
  | { readonly kind: 'list' }
  | { readonly kind: 'unresolved'; readonly params: CalibrationRouteParams }
  | { readonly kind: 'not-found'; readonly reason: 'PARTIAL_REFERENCE' | 'INVALID_ID' | 'CROSS_SCOPE' | 'COMPONENT_ROBOT_MISMATCH' | 'SET_COMPONENT_MISMATCH' }
  | { readonly kind: 'resolved'; readonly params: CalibrationRouteParams };

const stableId = /^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$/;

function normalizeParams(input: Partial<CalibrationRouteParams>): CalibrationRouteResolution {
  const values = [input.robotId, input.componentId, input.setId];
  if (values.every((value) => value === undefined || value.trim() === '')) return { kind: 'list' };
  if (values.some((value) => value === undefined || value.trim() === '')) return { kind: 'not-found', reason: 'PARTIAL_REFERENCE' };
  const params = {
    robotId: input.robotId?.trim() ?? '',
    componentId: input.componentId?.trim() ?? '',
    setId: input.setId?.trim() ?? '',
  };
  if (!stableId.test(params.robotId) || !stableId.test(params.componentId) || !stableId.test(params.setId)) {
    return { kind: 'not-found', reason: 'INVALID_ID' };
  }
  return { kind: 'unresolved', params };
}

export interface CalibrationReferenceResolver {
  isInCurrentScope(params: CalibrationRouteParams): boolean | Promise<boolean>;
  componentBelongsToRobot(params: CalibrationRouteParams): boolean | Promise<boolean>;
  setReferencesComponent(params: CalibrationRouteParams): boolean | Promise<boolean>;
}

export const calibrationsQueryCodec = {
  parse(search: URLSearchParams): CalibrationRouteResolution {
    return normalizeParams({
      robotId: search.get('robotId') ?? undefined,
      componentId: search.get('componentId') ?? undefined,
      setId: search.get('setId') ?? undefined,
    });
  },
  normalize: normalizeParams,
  build(params: CalibrationRouteParams): string {
    const normalized = normalizeParams(params);
    if (normalized.kind !== 'unresolved') throw new Error(`Invalid calibration route: ${normalized.kind}`);
    const search = new URLSearchParams();
    search.set('robotId', normalized.params.robotId);
    search.set('componentId', normalized.params.componentId);
    search.set('setId', normalized.params.setId);
    return search.toString();
  },
  async validateReferences(
    resolution: CalibrationRouteResolution,
    resolver: CalibrationReferenceResolver,
  ): Promise<CalibrationRouteResolution> {
    if (resolution.kind !== 'unresolved') return resolution;
    if (!await resolver.isInCurrentScope(resolution.params)) return { kind: 'not-found', reason: 'CROSS_SCOPE' };
    if (!await resolver.componentBelongsToRobot(resolution.params)) return { kind: 'not-found', reason: 'COMPONENT_ROBOT_MISMATCH' };
    if (!await resolver.setReferencesComponent(resolution.params)) return { kind: 'not-found', reason: 'SET_COMPONENT_MISMATCH' };
    return { kind: 'resolved', params: resolution.params };
  },
} as const;

export const routes = {
  calibrations: {
    build(params: CalibrationRouteParams): string {
      return `/settings/calibrations?${calibrationsQueryCodec.build(params)}`;
    },
  },
} as const;

