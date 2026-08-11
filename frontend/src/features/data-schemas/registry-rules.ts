import type { CompatibilityResult, DataSchemaVersion } from '../../entities/data-schema';

export const registryContractAssumptions = {
  scope: 'organization registry; project-scoped reference projections are validated separately',
  hash: 'server-owned SHA-256 over a versioned canonicalization algorithm',
  references: 'authorization-filtered server counts and cursor pages are authoritative',
  lifecycle: 'DRAFT -> VALIDATING -> PUBLISHED; PUBLISHED is immutable; deprecate is unavailable in V1',
  compatibility: 'server-owned verdict bound to baseline hash, target hash, mode, and ruleset version',
} as const;

export const compatibilityResults = ['BACKWARD', 'FORWARD', 'FULL', 'NONE'] as const;

export function projectCompatibilityResult(raw: string): CompatibilityResult {
  return compatibilityResults.includes(raw as (typeof compatibilityResults)[number])
    ? raw as (typeof compatibilityResults)[number]
    : 'UNKNOWN';
}

export interface CompatibilityEvidence {
  readonly status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN';
  readonly result: CompatibilityResult;
  readonly baselineHash: string;
  readonly targetHash: string;
  readonly mode: string;
  readonly rulesetVersion: string;
}

export function isCompatibilityEvidenceCurrent(
  evidence: CompatibilityEvidence,
  expected: Pick<CompatibilityEvidence, 'baselineHash' | 'targetHash' | 'mode' | 'rulesetVersion'>,
): boolean {
  return evidence.status === 'SUCCEEDED'
    && evidence.result !== 'UNKNOWN'
    && evidence.baselineHash === expected.baselineHash
    && evidence.targetHash === expected.targetHash
    && evidence.mode === expected.mode
    && evidence.rulesetVersion === expected.rulesetVersion;
}

export function canPublishSchema(
  schema: DataSchemaVersion,
  evidence: CompatibilityEvidence | null,
): { readonly allowed: boolean; readonly reasons: readonly string[] } {
  const reasons: string[] = [];
  if (schema.status !== 'DRAFT') reasons.push('只有 DRAFT Schema 可发布。');
  if (!schema.hash) reasons.push('缺少服务端 canonical hash。');
  if (!schema.allowedActions.includes('PUBLISH')) reasons.push('资源未声明 PUBLISH action。');
  reasons.push(...schema.blockedReasons.map((reason) => reason.message));
  if (!evidence || evidence.status !== 'SUCCEEDED') reasons.push('需要已完成的兼容性检查。');
  if (evidence?.result === 'NONE' || evidence?.result === 'UNKNOWN') reasons.push('兼容性结论不允许发布。');
  return { allowed: reasons.length === 0, reasons };
}

/** Schema source is deliberately returned only for a read-only code view, never telemetry. */
export function schemaTelemetryProjection(schema: DataSchemaVersion): Readonly<Record<string, string>> {
  return { schemaId: schema.schemaId, schemaVersion: schema.version, status: schema.status };
}

