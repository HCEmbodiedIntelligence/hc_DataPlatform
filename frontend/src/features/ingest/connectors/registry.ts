import { z } from 'zod';

export const robotConnectorSchema = z
  .object({
    kind: z.literal('ROBOT'),
    transport: z.enum(['HTTPS', 'MQTTS']),
    endpointRef: z.string().min(1),
    safeEndpointHint: z.string().max(300).nullable(),
    tlsProfileId: z.string().nullable(),
  })
  .strict();

export const edgeAgentConnectorSchema = z
  .object({
    kind: z.literal('EDGE_AGENT'),
    agentId: z.string().min(1),
    transport: z.enum(['OUTBOUND_HTTPS', 'MQTTS']),
    heartbeatPolicyId: z.string().min(1),
  })
  .strict();

export const ossImportConnectorSchema = z
  .object({
    kind: z.literal('OSS_IMPORT'),
    ossAccountAlias: z.string().min(1).max(100),
    bucketAlias: z.string().min(1).max(100),
    prefixHint: z.string().min(1).max(300),
    roleRef: z.string().min(1),
    sourceRegionCode: z.string().min(1),
  })
  .strict();

export const knownConnectorSchema = z.discriminatedUnion('kind', [
  robotConnectorSchema,
  edgeAgentConnectorSchema,
  ossImportConnectorSchema,
]);

export const unknownConnectorSchema = z
  .object({
    kind: z.literal('UNKNOWN'),
    rawType: z.string().min(1),
    safeProjection: z
      .object({
        displayName: z.string().max(200).nullable(),
        connectorFamily: z.string().max(100).nullable(),
        migrationHint: z.string().max(500).nullable(),
      })
      .strict(),
  })
  .strict();

export const connectorConfigurationSchema = z.union([knownConnectorSchema, unknownConnectorSchema]);
export type KnownConnectorConfiguration = z.infer<typeof knownConnectorSchema>;
export type UnknownConnectorConfiguration = z.infer<typeof unknownConnectorSchema>;
export type ConnectorConfiguration = z.infer<typeof connectorConfigurationSchema>;

export type WritableConnectorBinding =
  | { readonly kind: 'ROBOT'; readonly robotId: string }
  | { readonly kind: 'EDGE_AGENT'; readonly agentId: string }
  | { readonly kind: 'OSS_IMPORT'; readonly sourceAlias: string };

export interface ConnectorRegistryEntry {
  readonly kind: KnownConnectorConfiguration['kind'];
  readonly label: string;
  readonly supportsCredential: boolean;
  readonly editable: true;
}

export const connectorRegistry: Readonly<Record<KnownConnectorConfiguration['kind'], ConnectorRegistryEntry>> = {
  ROBOT: { kind: 'ROBOT', label: '机器人', supportsCredential: true, editable: true },
  EDGE_AGENT: { kind: 'EDGE_AGENT', label: '边缘代理', supportsCredential: true, editable: true },
  OSS_IMPORT: { kind: 'OSS_IMPORT', label: 'OSS 导入', supportsCredential: true, editable: true },
};

export function adaptConnectorConfiguration(raw: unknown): ConnectorConfiguration {
  const known = knownConnectorSchema.safeParse(raw);
  if (known.success) return known.data;

  const value = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {};
  const projection = typeof value.safe_projection === 'object' && value.safe_projection !== null
    ? (value.safe_projection as Record<string, unknown>)
    : {};
  return {
    kind: 'UNKNOWN',
    rawType: typeof value.raw_source_type === 'string'
      ? value.raw_source_type
      : typeof value.kind === 'string'
        ? value.kind
        : 'UNRECOGNIZED',
    safeProjection: {
      displayName: typeof projection.display_name === 'string' ? projection.display_name : null,
      connectorFamily: typeof projection.connector_family === 'string' ? projection.connector_family : null,
      migrationHint: typeof projection.migration_hint === 'string' ? projection.migration_hint : null,
    },
  };
}

export function isConnectorEditable(value: ConnectorConfiguration): value is KnownConnectorConfiguration {
  return value.kind !== 'UNKNOWN';
}

/** Explicit serializer keeps camelCase UI/domain values out of the snake_case wire contract. */
export function toWritableConnectorWire(configuration: KnownConnectorConfiguration): Record<string, unknown> {
  switch (configuration.kind) {
    case 'ROBOT':
      return {
        kind: configuration.kind,
        transport: configuration.transport,
        endpoint_ref: configuration.endpointRef,
        tls_profile_id: configuration.tlsProfileId,
      };
    case 'EDGE_AGENT':
      return {
        kind: configuration.kind,
        agent_id: configuration.agentId,
        transport: configuration.transport,
        heartbeat_policy_id: configuration.heartbeatPolicyId,
      };
    case 'OSS_IMPORT':
      return {
        kind: configuration.kind,
        oss_account_alias: configuration.ossAccountAlias,
        bucket_alias: configuration.bucketAlias,
        prefix_hint: configuration.prefixHint,
        role_ref: configuration.roleRef,
        source_region_code: configuration.sourceRegionCode,
      };
  }
}

export function toWritableBindingWire(binding: WritableConnectorBinding): Record<string, string> {
  switch (binding.kind) {
    case 'ROBOT': return { kind: binding.kind, robot_id: binding.robotId };
    case 'EDGE_AGENT': return { kind: binding.kind, agent_id: binding.agentId };
    case 'OSS_IMPORT': return { kind: binding.kind, source_alias: binding.sourceAlias };
  }
}
