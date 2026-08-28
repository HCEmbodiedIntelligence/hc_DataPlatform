import { defineQueryCodec } from "../../shared/routing/route-registry";

export interface RobotModelsSearch {
  readonly q?: string;
  readonly binding: "all" | "bound" | "unbound";
  readonly sort: "updatedAt:desc" | "updatedAt:asc" | "name:asc";
  readonly modelId?: string;
  readonly versionId?: string;
  readonly targetRobotId?: string;
  readonly targetRegionCode?: string;
  readonly detailTab:
    | "overview"
    | "assets"
    | "mapping"
    | "bindings"
    | "validations";
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
}

const defaults: RobotModelsSearch = {
  binding: "all",
  sort: "updatedAt:desc",
  detailTab: "overview",
  limit: 20,
};
const bindingValues = ["all", "bound", "unbound"] as const;
const sortValues = ["updatedAt:desc", "updatedAt:asc", "name:asc"] as const;
const detailTabs = [
  "overview",
  "assets",
  "mapping",
  "bindings",
  "validations",
] as const;

export const robotModelsQueryCodec = defineQueryCodec<RobotModelsSearch>({
  defaults,
  parse(sp) {
    const binding = bindingValues.includes(
      sp.get("binding") as RobotModelsSearch["binding"],
    )
      ? (sp.get("binding") as RobotModelsSearch["binding"])
      : defaults.binding;
    const sort = sortValues.includes(
      sp.get("sort") as RobotModelsSearch["sort"],
    )
      ? (sp.get("sort") as RobotModelsSearch["sort"])
      : defaults.sort;
    const detailTab = detailTabs.includes(
      sp.get("detailTab") as RobotModelsSearch["detailTab"],
    )
      ? (sp.get("detailTab") as RobotModelsSearch["detailTab"])
      : defaults.detailTab;
    const after = sp.get("after") || undefined;
    const before = sp.get("before") || undefined;
    const rawLimit = sp.get("limit");
    const parsedLimit = rawLimit === "50" ? 50 : rawLimit === "100" ? 100 : 20;
    const q = sp.get("q")?.trim().slice(0, 100);
    const modelId = sp.get("modelId") || undefined;
    const versionId = sp.get("versionId") || undefined;
    const targetRobotId = sp.get("targetRobotId") || undefined;
    const targetRegionCode = sp.get("targetRegionCode") || undefined;
    return {
      binding,
      sort,
      detailTab,
      limit: parsedLimit,
      ...(q ? { q } : {}),
      ...(modelId ? { modelId } : {}),
      ...(versionId ? { versionId } : {}),
      ...(targetRobotId ? { targetRobotId } : {}),
      ...(targetRegionCode ? { targetRegionCode } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
    };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    for (const key of [
      "q",
      "modelId",
      "versionId",
      "targetRobotId",
      "targetRegionCode",
      "after",
      "before",
    ] as const)
      if (merged[key]) sp.set(key, merged[key]);
    if (merged.binding !== defaults.binding) sp.set("binding", merged.binding);
    if (merged.sort !== defaults.sort) sp.set("sort", merged.sort);
    if (merged.detailTab !== defaults.detailTab)
      sp.set("detailTab", merged.detailTab);
    if (merged.limit !== defaults.limit) sp.set("limit", String(merged.limit));
    return sp;
  },
});
