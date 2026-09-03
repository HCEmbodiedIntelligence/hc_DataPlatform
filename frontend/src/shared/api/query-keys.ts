import { getShellState } from '../scope/shell-store';

export type SharedQueryKey = readonly [
  domain: string,
  scopeKey: string,
  resource: string,
  identityOrFilters: unknown,
  snapshotOrRevision: string | undefined,
];

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function assertSafeQueryKeyValue(value: unknown, seen: WeakSet<object>): void {
  if (value instanceof Date) throw new TypeError('Date values are forbidden in query keys');
  if (typeof value === 'function') throw new TypeError('Functions are forbidden in query keys');
  if (typeof value === 'string' && /^https:\/\//iu.test(value))
    throw new TypeError('Signed or remote URLs are forbidden in query keys');
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new TypeError('Cyclic values are forbidden in query keys');
    seen.add(value);
    for (const item of value) assertSafeQueryKeyValue(item, seen);
    return;
  }
  if (typeof value === 'object' && value !== null) {
    if (!isPlainRecord(value)) throw new TypeError('Only plain normalized objects may enter query keys');
    if (seen.has(value)) throw new TypeError('Cyclic values are forbidden in query keys');
    seen.add(value);
    for (const item of Object.values(value)) assertSafeQueryKeyValue(item, seen);
  }
}

function deepEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((item, index) => deepEqual(item, right[index]));
  }
  if (isPlainRecord(left) && isPlainRecord(right)) {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every((key, index) => key === rightKeys[index] && deepEqual(left[key], right[key]))
    );
  }
  return false;
}

export function normalizeFilters<T extends object>(
  filters: T,
  defaults: Readonly<Partial<T>>,
): Readonly<Partial<T>> {
  const filterRecord = filters as Record<string, unknown>;
  const defaultsRecord = defaults as Record<string, unknown>;
  const entries = Object.keys(filterRecord)
    .sort()
    .flatMap((key) => {
      const value = filterRecord[key];
      if (value === undefined || deepEqual(value, defaultsRecord[key])) return [];
      return [[key, value] as const];
    });
  return Object.freeze(Object.fromEntries(entries)) as Readonly<Partial<T>>;
}

export function makeQueryKey(
  domain: string,
  resource: string,
  identityOrFilters: unknown,
  snapshotOrRevision?: string,
): SharedQueryKey {
  assertSafeQueryKeyValue(identityOrFilters, new WeakSet<object>());
  assertSafeQueryKeyValue(snapshotOrRevision, new WeakSet<object>());
  return Object.freeze([
    domain,
    getShellState().scopeKey,
    resource,
    identityOrFilters,
    snapshotOrRevision,
  ]) as SharedQueryKey;
}
