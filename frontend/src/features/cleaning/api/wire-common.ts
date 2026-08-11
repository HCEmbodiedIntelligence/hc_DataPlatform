import { z } from 'zod';

export const idWireSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
export const decimalNsWireSchema = z.string().regex(/^(0|[1-9][0-9]*)$/);
export const signedNsWireSchema = z.string().regex(/^-?(0|[1-9][0-9]*)$/);
export const int64WireSchema = decimalNsWireSchema;
export const instantWireSchema = z.iso.datetime({ offset: true });
export const etagWireSchema = z.string().max(256).regex(/^"[^"\r\n]+"$/);
export const sha256WireSchema = z.string().regex(/^sha256:[0-9a-f]{64}$/);

export const scopeWireSchema = z.object({
  organization_id: idWireSchema,
  project_id: idWireSchema,
  region_code: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/).max(64),
}).strict();

export const blockedReasonWireSchema = z.object({
  code: z.string().min(1).max(96),
  message: z.string().min(1).max(2048),
}).strict();

export const principalSummaryWireSchema = z.object({
  id: idWireSchema,
  display_name: z.string().min(1).max(256),
}).strict();

export const pageInfoWireSchema = z.object({
  after: z.string().min(1).max(2048).nullable(),
  before: z.string().min(1).max(2048).nullable(),
  has_next: z.boolean(),
  has_previous: z.boolean(),
}).strict();

export function scopeEnvelopeWireSchema<T extends z.ZodType>(data: T) {
  return z.object({
    data,
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.literal('manual-cleaning.v1'),
  }).strict();
}

export interface ExpectedCleaningScope {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
}

export function assertCleaningScope(
  actual: z.infer<typeof scopeWireSchema>,
  expected: ExpectedCleaningScope,
): void {
  if (
    actual.organization_id !== expected.organizationId ||
    actual.project_id !== expected.projectId ||
    actual.region_code !== expected.regionCode
  ) {
    throw new Error('SCOPE_MISMATCH');
  }
}
