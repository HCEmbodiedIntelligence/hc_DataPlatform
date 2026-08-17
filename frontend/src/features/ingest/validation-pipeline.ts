import type { UnknownEnum } from '../../entities/data-source';

export const VALIDATION_STAGE_CODES = [
  'MANIFEST_SCHEMA',
  'OBJECT_EXISTENCE_SIZE',
  'OBJECT_SHA256',
  'ADAPTER_PARSE',
  'DATASET_SCHEMA_SEMANTIC',
  'ATOMIC_AVAILABILITY_COMMIT',
] as const;

export type KnownValidationStageCode = (typeof VALIDATION_STAGE_CODES)[number];
export type ValidationStageCode = KnownValidationStageCode | UnknownEnum;
export type ValidationStageStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PASSED'
  | 'FAILED'
  | 'SKIPPED'
  | 'CANCELLED'
  | UnknownEnum;

const VALIDATION_STAGE_LABELS: Readonly<Record<KnownValidationStageCode, string>> = {
  MANIFEST_SCHEMA: '清单结构校验',
  OBJECT_EXISTENCE_SIZE: '对象存在性与大小校验',
  OBJECT_SHA256: '对象 SHA-256 校验',
  ADAPTER_PARSE: '适配器解析',
  DATASET_SCHEMA_SEMANTIC: '数据集结构与语义校验',
  ATOMIC_AVAILABILITY_COMMIT: '原子可用性提交',
};

const VALIDATION_STAGE_STATUS_LABELS: Readonly<Record<string, string>> = {
  PENDING: '待执行',
  RUNNING: '校验中',
  PASSED: '已通过',
  FAILED: '未通过',
  SKIPPED: '已跳过',
  CANCELLED: '已取消',
};

export interface ValidationStage {
  readonly code: ValidationStageCode;
  readonly status: ValidationStageStatus;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
  readonly jobId: string | null;
  readonly findingCount: string;
  readonly retryable: boolean;
  readonly skipReason: string | null;
}

export type QuarantineDisposition =
  | 'OPEN'
  | 'REVERIFY_REQUESTED'
  | 'RELEASED'
  | 'REPLACED'
  | 'SUPERSEDED'
  | 'RETENTION_EXPIRED'
  | UnknownEnum;

const STAGE_STATUS_TRANSITIONS: Record<string, readonly string[]> = {
  PENDING: ['RUNNING', 'SKIPPED', 'CANCELLED'],
  RUNNING: ['PASSED', 'FAILED', 'CANCELLED'],
  PASSED: [],
  FAILED: [],
  SKIPPED: [],
  CANCELLED: [],
};

const QUARANTINE_TRANSITIONS: Record<string, readonly string[]> = {
  OPEN: ['REVERIFY_REQUESTED', 'REPLACED', 'RETENTION_EXPIRED'],
  REVERIFY_REQUESTED: ['RELEASED', 'SUPERSEDED'],
  RELEASED: [],
  REPLACED: [],
  SUPERSEDED: [],
  RETENTION_EXPIRED: [],
};

export function parseValidationStageCode(raw: string): ValidationStageCode {
  return (VALIDATION_STAGE_CODES as readonly string[]).includes(raw) ? (raw as KnownValidationStageCode) : { kind: 'UNKNOWN', raw };
}

export function validationStageLabel(code: ValidationStageCode): string {
  return typeof code === 'string' ? VALIDATION_STAGE_LABELS[code] : `未知校验阶段（${code.raw}）`;
}

export function validationStageStatusLabel(status: ValidationStageStatus): string {
  if (typeof status !== 'string') return `未知状态（${status.raw}）`;
  return VALIDATION_STAGE_STATUS_LABELS[status] ?? `未知状态（${status}）`;
}

export function validationStageSkipReasonLabel(reason: string): string {
  const blockedBy = /^blocked by (.+)$/.exec(reason);
  if (!blockedBy) return reason;
  const rawBlockingCode = blockedBy[1];
  if (!rawBlockingCode) return reason;
  const blockingCode = parseValidationStageCode(rawBlockingCode);
  return `被上游阶段“${validationStageLabel(blockingCode)}”阻断`;
}

export function validationStageOrder(code: ValidationStageCode): number {
  if (typeof code !== 'string') return Number.MAX_SAFE_INTEGER;
  return VALIDATION_STAGE_CODES.indexOf(code);
}

export function canTransitionValidationStage(from: ValidationStageStatus, to: ValidationStageStatus): boolean {
  if (typeof from !== 'string' || typeof to !== 'string') return false;
  return STAGE_STATUS_TRANSITIONS[from]?.includes(to) ?? false;
}

export function canTransitionQuarantine(from: QuarantineDisposition, to: QuarantineDisposition): boolean {
  if (typeof from !== 'string' || typeof to !== 'string') return false;
  return QUARANTINE_TRANSITIONS[from]?.includes(to) ?? false;
}

export function validatePipeline(stages: readonly ValidationStage[]): readonly string[] {
  const errors: string[] = [];
  let lastOrder = -1;
  for (const stage of stages) {
    const order = validationStageOrder(stage.code);
    if (order !== Number.MAX_SAFE_INTEGER && order < lastOrder) errors.push('VALIDATION_STAGE_ORDER_INVALID');
    if (order !== Number.MAX_SAFE_INTEGER) lastOrder = order;
    if (stage.status === 'SKIPPED' && !stage.skipReason) errors.push('SKIPPED_STAGE_REQUIRES_REASON');
    if (typeof stage.code !== 'string') errors.push('UNKNOWN_VALIDATION_STAGE');
  }
  return errors;
}

export function canRequestQuarantineRelease(disposition: QuarantineDisposition): boolean {
  return disposition === 'OPEN';
}
