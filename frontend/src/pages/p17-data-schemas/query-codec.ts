import { defineQueryCodec } from "../../shared/routing/route-registry";
import {
  dataSchemasQueryCodec,
  dataSchemaDetailTabs,
  normalizeSchemaVersion,
  type DataSchemaDetailTab,
} from "../../features/data-schemas/routing";

export interface DataSchemasSearch {
  readonly tab: "registry" | "snapshots" | "compatibility";
  readonly q?: string;
  readonly status?: "DRAFT" | "PUBLISHED";
  readonly logicalType?: string;
  readonly schemaId?: string;
  readonly schemaVersion?: string;
  readonly componentId?: string;
  readonly detailTab: DataSchemaDetailTab;
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
}

const defaults: DataSchemasSearch = {
  tab: "registry",
  detailTab: "fields",
  limit: 20,
};
const tabs = ["registry", "snapshots", "compatibility"] as const;

export const pageDataSchemasQueryCodec = defineQueryCodec<DataSchemasSearch>({
  defaults,
  parse(sp) {
    const tab = tabs.includes(sp.get("tab") as DataSchemasSearch["tab"])
      ? (sp.get("tab") as DataSchemasSearch["tab"])
      : defaults.tab;
    const schemaId = sp.get("schemaId")?.trim() || undefined;
    const schemaVersion = sp.get("schemaVersion")
      ? (normalizeSchemaVersion(sp.get("schemaVersion") ?? "") ?? undefined)
      : undefined;
    const componentId =
      schemaId && schemaVersion
        ? sp.get("componentId")?.trim() || undefined
        : undefined;
    const q = sp.get("q")?.trim().slice(0, 100);
    const status =
      sp.get("status") === "DRAFT" || sp.get("status") === "PUBLISHED"
        ? (sp.get("status") as DataSchemasSearch["status"])
        : undefined;
    const logicalType =
      sp.get("logicalType")?.trim().slice(0, 128) || undefined;
    const after = sp.get("after") || undefined;
    const before = sp.get("before") || undefined;
    const rawLimit = sp.get("limit");
    const parsedLimit = rawLimit === "50" ? 50 : rawLimit === "100" ? 100 : 20;
    const detailTab = dataSchemaDetailTabs.includes(
      sp.get("detailTab") as DataSchemaDetailTab,
    )
      ? (sp.get("detailTab") as DataSchemaDetailTab)
      : "fields";
    return {
      tab,
      detailTab,
      limit: parsedLimit,
      ...(q ? { q } : {}),
      ...(status ? { status } : {}),
      ...(logicalType ? { logicalType } : {}),
      ...(schemaId && schemaVersion ? { schemaId, schemaVersion } : {}),
      ...(componentId ? { componentId } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
    };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    for (const key of [
      "q",
      "status",
      "logicalType",
      "schemaId",
      "schemaVersion",
      "componentId",
      "after",
      "before",
    ] as const)
      if (merged[key]) sp.set(key, merged[key]);
    if (merged.tab !== defaults.tab) sp.set("tab", merged.tab);
    if (merged.detailTab !== defaults.detailTab)
      sp.set("detailTab", merged.detailTab);
    if (merged.limit !== defaults.limit) sp.set("limit", String(merged.limit));
    return sp;
  },
});

export { dataSchemasQueryCodec };
