import { set, type FieldErrors, type FieldValues, type Resolver } from 'react-hook-form';
import type { ZodType } from 'zod';

const unsafePathSegment = new Set(['__proto__', 'constructor', 'prototype']);

function issuePath(segments: readonly PropertyKey[]): string | null {
  if (segments.length === 0) return 'root.schema';
  const normalized: string[] = [];
  for (const segment of segments) {
    if (typeof segment !== 'string' && typeof segment !== 'number') return null;
    const value = String(segment);
    if (value.length === 0 || unsafePathSegment.has(value)) return null;
    normalized.push(value);
  }
  return normalized.join('.');
}

/**
 * Keeps Zod as the only validation schema while React Hook Form owns form state.
 * The UI adapters intentionally do not accept Ant Design Form rules.
 */
export function createZodResolver<TFieldValues extends FieldValues>(
  schema: ZodType<TFieldValues>,
): Resolver<TFieldValues> {
  return async (values) => {
    const result = await schema.safeParseAsync(values);
    if (result.success) return { values: result.data, errors: {} };

    const errors: FieldErrors<TFieldValues> = {};
    for (const issue of result.error.issues) {
      const path = issuePath(issue.path);
      if (path === null) continue;
      set(errors, path, { type: issue.code, message: issue.message });
    }
    return { values: {}, errors };
  };
}
