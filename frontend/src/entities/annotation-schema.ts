import { z } from 'zod';

export type AnnotationFieldKind = 'string' | 'number' | 'boolean' | 'enum' | 'multi-enum' | 'time-point' | 'time-range';

export interface AnnotationFieldDefinition {
  readonly key: string;
  readonly label: string;
  readonly description?: string;
  readonly type: AnnotationFieldKind | (string & {});
  readonly required?: boolean;
  readonly readOnly?: boolean;
  /** Only explicitly non-sensitive, controlled fields may opt into local recovery. */
  readonly localCache?: boolean;
  readonly options?: ReadonlyArray<{ readonly value: string; readonly label: string }>;
  readonly min?: number;
  readonly max?: number;
  readonly maxLength?: number;
}

export interface AnnotationFormDefinition {
  readonly schemaVersionId: string;
  readonly title: string;
  readonly fields: readonly AnnotationFieldDefinition[];
}

export interface CompiledAnnotationFormSchema {
  readonly schema: z.ZodObject<z.ZodRawShape>;
  readonly unsupportedKeys: ReadonlySet<string>;
}

export function compileAnnotationFormSchema(definition: AnnotationFormDefinition): CompiledAnnotationFormSchema {
  const shape: Record<string, z.ZodType> = {};
  const unsupported = new Set<string>();
  for (const field of definition.fields) {
    let fieldSchema: z.ZodType;
    if (field.type === 'string') fieldSchema = z.string().max(field.maxLength ?? 2048);
    else if (field.type === 'number') fieldSchema = z.number().min(field.min ?? -Number.MAX_VALUE).max(field.max ?? Number.MAX_VALUE);
    else if (field.type === 'boolean') fieldSchema = z.boolean();
    else if (field.type === 'enum') fieldSchema = z.string().refine((value) => field.options?.some((option) => option.value === value) ?? false, '请选择合同允许的值');
    else if (field.type === 'multi-enum') fieldSchema = z.array(z.string()).refine((values) => values.every((value) => field.options?.some((option) => option.value === value) ?? false), '包含合同外的值');
    else if (field.type === 'time-point') fieldSchema = z.string().regex(/^(0|[1-9][0-9]*)$/, '必须是纳秒十进制字符串');
    else if (field.type === 'time-range') fieldSchema = z.object({ startNs: z.string().regex(/^(0|[1-9][0-9]*)$/), endNs: z.string().regex(/^(0|[1-9][0-9]*)$/) }).refine((range) => BigInt(range.startNs) < BigInt(range.endNs), { path: ['endNs'], message: '结束时间必须晚于开始时间' });
    else {
      fieldSchema = z.unknown();
      unsupported.add(field.key);
    }
    if (!field.required) fieldSchema = fieldSchema.optional();
    shape[field.key] = fieldSchema;
  }
  return { schema: z.object(shape), unsupportedKeys: unsupported };
}
