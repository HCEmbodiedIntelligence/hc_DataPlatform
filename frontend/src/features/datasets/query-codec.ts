export type QueryInput = string | URLSearchParams;

export type DefinedQueryCodec<TSearch, TChanges> = Readonly<{
  parse(input: QueryInput): TSearch;
  build(input: TChanges): URLSearchParams;
  canonicalize(input: QueryInput): string;
  withChanges(current: TSearch, changes: Partial<TChanges>, scopeChanged?: boolean): TSearch;
}>;

export function defineQueryCodec<TSearch, TChanges>(
  codec: DefinedQueryCodec<TSearch, TChanges>,
): DefinedQueryCodec<TSearch, TChanges> {
  return Object.freeze(codec);
}

export function asSearchParams(input: QueryInput): URLSearchParams {
  return input instanceof URLSearchParams
    ? new URLSearchParams(input)
    : new URLSearchParams(input.startsWith('?') ? input.slice(1) : input);
}

export function cleanText(value: string | null | undefined, max = 256): string | undefined {
  const normalized = value?.trim().normalize('NFC');
  return normalized && normalized.length <= max && !/\p{Cc}/u.test(normalized)
    ? normalized
    : undefined;
}

export function cleanId(value: string | null | undefined, pattern: RegExp): string | undefined {
  const normalized = cleanText(value, 128);
  return normalized && pattern.test(normalized) ? normalized : undefined;
}

export function cleanCursor(value: string | null | undefined): string | undefined {
  const normalized = cleanText(value, 2048);
  return normalized && !normalized.includes('/') && !normalized.includes('\\')
    ? normalized
    : undefined;
}

export function enumValue<const T extends readonly string[]>(
  value: string | null | undefined,
  values: T,
): T[number] | undefined {
  return value && (values as readonly string[]).includes(value) ? (value as T[number]) : undefined;
}

export function safeInternalReturnTo(
  value: string | null | undefined,
  allowedPrefixes: readonly string[],
): string | undefined {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\'))
    return undefined;
  try {
    const parsed = new URL(value, 'https://local.invalid');
    return parsed.origin === 'https://local.invalid' &&
      parsed.hash === '' &&
      allowedPrefixes.some(
        (prefix) => parsed.pathname === prefix || parsed.pathname.startsWith(`${prefix}/`),
      )
      ? `${parsed.pathname}${parsed.search}`
      : undefined;
  } catch {
    return undefined;
  }
}

export function canonicalCursorPair(params: URLSearchParams): { after?: string; before?: string } {
  const after = cleanCursor(params.get('after'));
  const before = cleanCursor(params.get('before'));
  return after && before ? {} : { ...(after ? { after } : {}), ...(before ? { before } : {}) };
}

export function valuesChanged<T extends object>(
  current: T,
  changes: Partial<T>,
  keys: readonly (keyof T)[],
): boolean {
  return keys.some(
    (key) =>
      Object.prototype.hasOwnProperty.call(changes, key) &&
      JSON.stringify(current[key]) !== JSON.stringify(changes[key]),
  );
}
