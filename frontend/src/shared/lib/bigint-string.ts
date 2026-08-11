declare const int64StringBrand: unique symbol;

export type Int64String = string & { readonly [int64StringBrand]: 'Int64String' };

const MIN_INT64 = -(1n << 63n);
const MAX_INT64 = (1n << 63n) - 1n;
const canonicalInteger = /^(?:0|-?[1-9]\d*)$/u;

export function isInt64String(value: unknown): value is Int64String {
  if (typeof value !== 'string' || !canonicalInteger.test(value)) return false;
  const parsed = BigInt(value);
  return parsed >= MIN_INT64 && parsed <= MAX_INT64;
}

export function int64String(value: string): Int64String {
  if (!isInt64String(value)) throw new RangeError('Expected a canonical signed int64 decimal string');
  return value;
}

export function int64FromBigInt(value: bigint): Int64String {
  return int64String(value.toString());
}

export function int64ToBigInt(value: Int64String): bigint {
  return BigInt(value);
}

export function addInt64(left: Int64String, right: Int64String): Int64String {
  return int64FromBigInt(BigInt(left) + BigInt(right));
}

export function subtractInt64(left: Int64String, right: Int64String): Int64String {
  return int64FromBigInt(BigInt(left) - BigInt(right));
}

export function compareInt64(left: Int64String, right: Int64String): -1 | 0 | 1 {
  const leftBigInt = BigInt(left);
  const rightBigInt = BigInt(right);
  return leftBigInt < rightBigInt ? -1 : leftBigInt > rightBigInt ? 1 : 0;
}
