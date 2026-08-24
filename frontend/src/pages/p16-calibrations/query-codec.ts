import { defineQueryCodec } from "../../shared/routing/route-registry";
import { calibrationsQueryCodec } from "../../features/calibrations/routing";

export interface CalibrationsSearch {
  readonly tab: "sets" | "jobs" | "reports";
  readonly robotId?: string;
  readonly componentId?: string;
  readonly setId?: string;
  readonly version?: string;
  readonly section:
    | "overview"
    | "intrinsics"
    | "transforms"
    | "timeCalibrations"
    | "jointCalibrations";
  readonly recordId?: string;
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
}

const defaults: CalibrationsSearch = {
  tab: "sets",
  section: "overview",
  limit: 20,
};
const tabs = ["sets", "jobs", "reports"] as const;
const sections = [
  "overview",
  "intrinsics",
  "transforms",
  "timeCalibrations",
  "jointCalibrations",
] as const;

export const pageCalibrationsQueryCodec = defineQueryCodec<CalibrationsSearch>({
  defaults,
  parse(sp) {
    const tab = tabs.includes(sp.get("tab") as CalibrationsSearch["tab"])
      ? (sp.get("tab") as CalibrationsSearch["tab"])
      : defaults.tab;
    const section = sections.includes(
      sp.get("section") as CalibrationsSearch["section"],
    )
      ? (sp.get("section") as CalibrationsSearch["section"])
      : defaults.section;
    const relation = calibrationsQueryCodec.parse(sp);
    const params =
      relation.kind === "unresolved" || relation.kind === "resolved"
        ? relation.params
        : undefined;
    const after = sp.get("after") || undefined;
    const before = sp.get("before") || undefined;
    const rawLimit = sp.get("limit");
    const parsedLimit = rawLimit === "50" ? 50 : rawLimit === "100" ? 100 : 20;
    const recordId =
      tab === "sets" && params && section !== "overview"
        ? sp.get("recordId") || undefined
        : undefined;
    const version = /^(0|[1-9]\d*)$/.test(sp.get("version") || "")
      ? sp.get("version") || undefined
      : undefined;
    return {
      tab,
      section,
      limit: parsedLimit,
      ...(params ?? {}),
      ...(version ? { version } : {}),
      ...(recordId ? { recordId } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
    };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    for (const key of [
      "robotId",
      "componentId",
      "setId",
      "version",
      "recordId",
      "after",
      "before",
    ] as const)
      if (merged[key]) sp.set(key, merged[key]);
    if (merged.tab !== defaults.tab) sp.set("tab", merged.tab);
    if (merged.section !== defaults.section) sp.set("section", merged.section);
    if (merged.limit !== defaults.limit) sp.set("limit", String(merged.limit));
    return sp;
  },
});

export { calibrationsQueryCodec };
