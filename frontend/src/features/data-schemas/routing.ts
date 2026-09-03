export const dataSchemaDetailTabs = ['fields', 'encoding', 'compatibility', 'references'] as const;
export type DataSchemaDetailTab = (typeof dataSchemaDetailTabs)[number];

export type DataSchemaRouteParams = {
  readonly schemaId: string;
  readonly schemaVersion: string;
  readonly componentId: string;
  readonly detailTab: DataSchemaDetailTab;
};

export type DataSchemaRouteResolution =
  | { readonly kind: 'list' }
  | { readonly kind: 'unresolved'; readonly params: DataSchemaRouteParams }
  | { readonly kind: 'resolved'; readonly params: DataSchemaRouteParams }
  | { readonly kind: 'not-found'; readonly reason: 'PARTIAL_REFERENCE' | 'INVALID_ID' | 'INVALID_VERSION' | 'CROSS_SCOPE' | 'COMPONENT_SCHEMA_MISMATCH' };

const stableId = /^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$/;

export function normalizeSchemaVersion(raw: string): string | null {
  const withoutPrefix = raw.trim().replace(/^[vV]/, '');
  if (!/^\d+$/.test(withoutPrefix)) return null;
  const normalized = withoutPrefix.replace(/^0+(?=\d)/, '');
  return normalized !== '0' ? normalized : null;
}

function normalize(input: Partial<Omit<DataSchemaRouteParams, 'detailTab'>> & { readonly detailTab?: string }): DataSchemaRouteResolution {
  const hasAnyIdentity = [input.schemaId, input.schemaVersion, input.componentId].some((value) => value !== undefined && value.trim() !== '');
  if (!hasAnyIdentity) return { kind: 'list' };
  if (!input.schemaId?.trim() || !input.schemaVersion?.trim() || !input.componentId?.trim()) {
    return { kind: 'not-found', reason: 'PARTIAL_REFERENCE' };
  }
  const schemaId = input.schemaId.trim();
  const componentId = input.componentId.trim();
  if (!stableId.test(schemaId) || !stableId.test(componentId)) return { kind: 'not-found', reason: 'INVALID_ID' };
  const schemaVersion = normalizeSchemaVersion(input.schemaVersion);
  if (!schemaVersion) return { kind: 'not-found', reason: 'INVALID_VERSION' };
  const detailTab = dataSchemaDetailTabs.includes(input.detailTab as DataSchemaDetailTab)
    ? input.detailTab as DataSchemaDetailTab
    : 'fields';
  return { kind: 'unresolved', params: { schemaId, schemaVersion, componentId, detailTab } };
}

export interface DataSchemaReferenceResolver {
  isInCurrentScope(params: DataSchemaRouteParams): boolean | Promise<boolean>;
  componentReferencesSchemaVersion(params: DataSchemaRouteParams): boolean | Promise<boolean>;
}

export const dataSchemasQueryCodec = {
  parse(search: URLSearchParams): DataSchemaRouteResolution {
    return normalize({
      schemaId: search.get('schemaId') ?? undefined,
      schemaVersion: search.get('schemaVersion') ?? undefined,
      componentId: search.get('componentId') ?? undefined,
      detailTab: search.get('detailTab') ?? undefined,
    });
  },
  normalize,
  build(params: DataSchemaRouteParams): string {
    const normalized = normalize(params);
    if (normalized.kind !== 'unresolved') throw new Error(`Invalid data schema route: ${normalized.kind}`);
    const search = new URLSearchParams();
    search.set('schemaId', normalized.params.schemaId);
    search.set('schemaVersion', normalized.params.schemaVersion);
    search.set('componentId', normalized.params.componentId);
    search.set('detailTab', normalized.params.detailTab);
    return search.toString();
  },
  async validateReferences(
    resolution: DataSchemaRouteResolution,
    resolver: DataSchemaReferenceResolver,
  ): Promise<DataSchemaRouteResolution> {
    if (resolution.kind !== 'unresolved') return resolution;
    if (!await resolver.isInCurrentScope(resolution.params)) return { kind: 'not-found', reason: 'CROSS_SCOPE' };
    if (!await resolver.componentReferencesSchemaVersion(resolution.params)) return { kind: 'not-found', reason: 'COMPONENT_SCHEMA_MISMATCH' };
    return { kind: 'resolved', params: resolution.params };
  },
} as const;

export const routes = {
  dataSchemas: {
    build(params: DataSchemaRouteParams): string {
      return `/settings/data-schemas?${dataSchemasQueryCodec.build(params)}`;
    },
  },
} as const;
