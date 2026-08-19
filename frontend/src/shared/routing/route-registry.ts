import { matchPath } from "react-router-dom";

export interface RegisteredRoute {
  path?: string;
  pattern?: string;
  navigationOwnerPageId?: string;
  navigationOwnerGroupId?: string;
}

export type PageRouteRegistration = unknown;

const canonicalPatterns = new Set<string>([
  "/dashboard",
  "/ingest/sources",
  "/ingest/uploads",
  "/ingest/uploads/new",
  "/ingest/uploads/records",
  "/ingest/uploads/:uploadId",
  "/datasets",
  "/datasets/:datasetId",
  "/datasets/:datasetId/versions/:versionId",
  "/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view",
  "/annotations",
  "/annotations/annotate",
  "/annotations/revisions",
  "/annotations/tag-review",
  "/annotations/tasks/:taskId",
  "/manual/issues",
  "/manual/drafts",
  "/manual/drafts/:draftId",
  "/storage/overview",
  "/storage/lifecycle",
  "/settings/robot-models",
  "/settings/robots",
  "/settings/calibrations",
  "/settings/data-schemas",
  "/settings/access",
  "/settings/audit",
]);

const pageRoutes = new Map<string, Set<string>>();

function registrationPaths(
  routes: PageRouteRegistration,
  seen = new WeakSet<object>(),
): string[] {
  if (typeof routes === "string") return routes.startsWith("/") ? [routes] : [];
  if (typeof routes !== "object" || routes === null) return [];
  if (seen.has(routes)) return [];
  seen.add(routes);
  if (Array.isArray(routes))
    return routes.flatMap((route) => registrationPaths(route, seen));
  const record = routes as Record<string, unknown>;
  const direct = [record.path, record.pattern].flatMap((path) =>
    typeof path === "string" && path.startsWith("/") ? [path] : [],
  );
  const routeRecord =
    "path" in record || "index" in record || "children" in record;
  const nestedValues = routeRecord
    ? [record.children]
    : Object.entries(record)
        .filter(([key]) => key !== "path" && key !== "pattern")
        .map(([, route]) => route);
  const nested = nestedValues.flatMap((route) =>
    registrationPaths(route, seen),
  );
  return [...direct, ...nested];
}

export function registerPageRoutes(
  pageId: string,
  routes: PageRouteRegistration,
): void {
  const paths = new Set(pageRoutes.get(pageId) ?? []);
  for (const path of registrationPaths(routes)) paths.add(path);
  pageRoutes.set(pageId, paths);
  for (const path of paths) canonicalPatterns.add(path);
}

export function safeReturnTo(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//"))
    return null;
  if (
    raw.includes("\\") ||
    [...raw].some((character) => (character.codePointAt(0) ?? 32) < 32)
  )
    return null;
  try {
    const parsed = new URL(raw, "https://application.invalid");
    if (parsed.origin !== "https://application.invalid") return null;
    const allowed = [...canonicalPatterns].some((pattern) =>
      Boolean(matchPath({ path: pattern, end: true }, parsed.pathname)),
    );
    return allowed ? `${parsed.pathname}${parsed.search}${parsed.hash}` : null;
  } catch {
    return null;
  }
}

export interface QueryFieldCodec {
  parse: (raw: string) => unknown;
  serialize?: (value: unknown) => string | null;
}

export interface DeclarativeQueryCodecConfig<T extends object> {
  defaults: T;
  allowedKeys?: readonly (keyof T & string)[];
  fields?: Readonly<Record<string, QueryFieldCodec>>;
  cursorResetKeys?: readonly (keyof T & string)[];
  normalize?: (value: T) => T;
  onDiagnostic?: (diagnostic: {
    code: "UNKNOWN_QUERY_KEY";
    key: string;
  }) => void;
}

export interface CustomQueryCodecConfig<
  T extends object,
  TBuild extends string | URLSearchParams = string | URLSearchParams,
> {
  defaults: T;
  parse: (params: URLSearchParams) => T;
  build: (value: Partial<T>) => TBuild;
  allowedKeys?: readonly (keyof T & string)[];
  cursorResetKeys?: readonly (keyof T & string)[];
  normalize?: (value: T) => T;
  onDiagnostic?: (diagnostic: {
    code: "UNKNOWN_QUERY_KEY";
    key: string;
  }) => void;
}

export type QueryCodecConfig<T extends object> =
  | DeclarativeQueryCodecConfig<T>
  | CustomQueryCodecConfig<T>;

function inputsEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  return JSON.stringify(left) === JSON.stringify(right);
}

function encodeQueryValue(value: unknown): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  if (
    Array.isArray(value) &&
    value.every((entry) =>
      ["string", "number", "boolean"].includes(typeof entry),
    )
  ) {
    return value.join(",");
  }
  throw new TypeError("Query codec values require an explicit serializer");
}

function asSearchParams(
  raw:
    | string
    | URLSearchParams
    | Readonly<Record<string, string | readonly string[]>>,
): URLSearchParams {
  if (raw instanceof URLSearchParams) return new URLSearchParams(raw);
  if (typeof raw === "string")
    return new URLSearchParams(raw.startsWith("?") ? raw.slice(1) : raw);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(raw)) {
    const entries: readonly string[] =
      typeof value === "string" ? [value] : value;
    for (const entry of entries) params.append(key, entry);
  }
  return params;
}

type QueryInput =
  | string
  | URLSearchParams
  | Readonly<Record<string, string | readonly string[]>>;

export function defineQueryCodec<
  T extends object,
  TBuild extends string | URLSearchParams = string | URLSearchParams,
>(
  cfg: CustomQueryCodecConfig<T, TBuild>,
): {
  parse: (raw: QueryInput) => T;
  build: (value: Partial<T>, previous?: Partial<T>) => TBuild;
  normalize: (value: Partial<T>, previous?: Partial<T>) => T;
};
export function defineQueryCodec<T extends object>(
  cfg: DeclarativeQueryCodecConfig<T>,
): {
  parse: (raw: QueryInput) => T;
  build: (value: Partial<T>, previous?: Partial<T>) => string;
  normalize: (value: Partial<T>, previous?: Partial<T>) => T;
};
export function defineQueryCodec<T extends object>(
  cfg: QueryCodecConfig<T>,
): {
  parse: (raw: QueryInput) => T;
  build: (value: Partial<T>, previous?: Partial<T>) => string | URLSearchParams;
  normalize: (value: Partial<T>, previous?: Partial<T>) => T;
} {
  const allowedKeys = new Set<string>(
    cfg.allowedKeys ?? Object.keys(cfg.defaults),
  );
  const acceptsCustomShape = "parse" in cfg && "build" in cfg;
  const configuredResetKeys = cfg.cursorResetKeys?.map(String);

  const emitUnknownKey = (key: string) => {
    const diagnostic = { code: "UNKNOWN_QUERY_KEY" as const, key };
    if (cfg.onDiagnostic) cfg.onDiagnostic(diagnostic);
    else console.warn("query_codec_diagnostic", diagnostic);
  };

  const normalize = (value: Partial<T>, previous?: Partial<T>): T => {
    const merged: Record<string, unknown> = {
      ...(cfg.defaults as Record<string, unknown>),
    };
    for (const [key, entry] of Object.entries(value)) {
      if (acceptsCustomShape || allowedKeys.has(key)) merged[key] = entry;
    }
    if (
      previous !== undefined &&
      (
        configuredResetKeys ??
        [
          ...new Set([
            ...allowedKeys,
            ...Object.keys(value),
            ...Object.keys(previous),
          ]),
        ].filter((key) => !["after", "before"].includes(key))
      ).some((key) => {
        const current = value as Record<string, unknown>;
        const prior = previous as Record<string, unknown>;
        return !inputsEqual(current[key], prior[key]);
      })
    ) {
      delete merged.after;
      delete merged.before;
    }
    if (merged.after !== undefined && merged.before !== undefined)
      delete merged.before;
    const normalized = merged as T;
    return cfg.normalize ? cfg.normalize(normalized) : normalized;
  };

  if ("parse" in cfg && "build" in cfg) {
    const parseCustom = (raw: QueryInput): T => {
      const params = asSearchParams(raw);
      const accessed = new Set<string>();
      class TrackedSearchParams extends URLSearchParams {
        override get(key: string): string | null {
          accessed.add(key);
          return super.get(key);
        }

        override getAll(key: string): string[] {
          accessed.add(key);
          return super.getAll(key);
        }

        override has(key: string, value?: string): boolean {
          accessed.add(key);
          return value === undefined ? super.has(key) : super.has(key, value);
        }
      }
      const tracked = new TrackedSearchParams(params);
      const parsed = cfg.parse(tracked);
      for (const key of new Set(params.keys())) {
        if (!accessed.has(key)) emitUnknownKey(key);
      }
      return cfg.normalize ? cfg.normalize(parsed) : parsed;
    };
    const buildCustom = (
      value: Partial<T>,
      previous?: Partial<T>,
    ): string | URLSearchParams => {
      const next = normalize(value, previous);
      return cfg.build(next);
    };
    const normalizeCustom = (value: Partial<T>, previous?: Partial<T>): T => {
      const merged = normalize(value, previous);
      return parseCustom(cfg.build(merged).toString());
    };
    return {
      parse: parseCustom,
      build: buildCustom,
      normalize: normalizeCustom,
    };
  }

  const parse = (
    raw:
      | string
      | URLSearchParams
      | Readonly<Record<string, string | readonly string[]>>,
  ): T => {
    const params = asSearchParams(raw);
    const parsed: Record<string, unknown> = {};
    for (const [key, value] of params.entries()) {
      if (!allowedKeys.has(key)) {
        emitUnknownKey(key);
        continue;
      }
      parsed[key] = cfg.fields?.[key]?.parse(value) ?? value;
    }
    return normalize(parsed as Partial<T>);
  };

  const build = (value: Partial<T>, previous?: Partial<T>): string => {
    const normalized = normalize(value, previous);
    const params = new URLSearchParams();
    for (const key of [...allowedKeys].sort()) {
      const normalizedRecord = normalized as Record<string, unknown>;
      const defaultsRecord = cfg.defaults as Record<string, unknown>;
      const entry = normalizedRecord[key];
      if (
        entry === undefined ||
        entry === null ||
        inputsEqual(entry, defaultsRecord[key])
      )
        continue;
      const encoded =
        cfg.fields?.[key]?.serialize?.(entry) ?? encodeQueryValue(entry);
      if (encoded !== null) params.set(key, encoded);
    }
    return params.toString();
  };

  return { parse, build, normalize };
}
