import { defineQueryCodec } from "../../shared/routing/route-registry";

export interface RobotsSearch {
  readonly q?: string;
  readonly modelId?: string;
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
}

const defaults: RobotsSearch = { limit: 20 };

export const robotsQueryCodec = defineQueryCodec<RobotsSearch>({
  defaults,
  parse(sp) {
    const q = sp.get("q")?.trim().slice(0, 100);
    const modelId = sp.get("modelId") || undefined;
    const after = sp.get("after") || undefined;
    const before = sp.get("before") || undefined;
    const rawLimit = sp.get("limit");
    const parsedLimit = rawLimit === "50" ? 50 : rawLimit === "100" ? 100 : 20;
    return {
      limit: parsedLimit,
      ...(q ? { q } : {}),
      ...(modelId ? { modelId } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
    };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    for (const key of ["q", "modelId", "after", "before"] as const)
      if (merged[key]) sp.set(key, merged[key]);
    if (merged.limit !== defaults.limit) sp.set("limit", String(merged.limit));
    return sp;
  },
});
