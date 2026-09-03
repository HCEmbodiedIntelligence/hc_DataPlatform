export function camelToSnake(value: string): string {
  return value.replace(/[A-Z]/gu, (letter) => `_${letter.toLowerCase()}`);
}

export function snakeToCamel(value: string): string {
  return value.replace(/_([a-z0-9])/gu, (_match, letter: string) => letter.toUpperCase());
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

export function keysToSnake(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(keysToSnake);
  if (!isPlainRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, entry]) => [camelToSnake(key), keysToSnake(entry)]),
  );
}

export function keysToCamel(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(keysToCamel);
  if (!isPlainRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, entry]) => [snakeToCamel(key), keysToCamel(entry)]),
  );
}
