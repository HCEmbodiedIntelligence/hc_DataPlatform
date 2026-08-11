export const UNKNOWN = 'UNKNOWN' as const;
export type UnknownEnum = typeof UNKNOWN;

export function mapUnknownEnum<const T extends readonly string[]>(
  value: unknown,
  knownValues: T,
): T[number] | UnknownEnum {
  return typeof value === 'string' && knownValues.includes(value as T[number])
    ? (value as T[number])
    : UNKNOWN;
}

export function isKnownEnumValue<const T extends readonly string[]>(
  value: T[number] | UnknownEnum,
  knownValues: T,
): value is T[number] {
  return value !== UNKNOWN && knownValues.includes(value);
}
