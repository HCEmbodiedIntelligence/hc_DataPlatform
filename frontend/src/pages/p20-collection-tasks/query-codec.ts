import { defineQueryCodec } from "../../shared/routing/route-registry";
import type { CollectionTaskStatus } from "./api";

export type CollectionTaskStatusFilter = "ALL" | CollectionTaskStatus;
export type CollectionTaskDrawerState =
  | { readonly mode: "create" }
  | { readonly mode: "edit"; readonly taskId: string };

export interface CollectionTaskSearch {
  readonly status: CollectionTaskStatusFilter;
  readonly query: string;
  readonly type: string;
  readonly cursor?: string;
  readonly drawer?: CollectionTaskDrawerState;
}

const statuses = new Set<CollectionTaskStatusFilter>([
  "ALL",
  "ACTIVE",
  "CLOSED",
]);
const stableIdentifier = /^[A-Za-z0-9._-]{1,128}$/u;

export const collectionTaskSearchDefaults: CollectionTaskSearch = {
  status: "ALL",
  query: "",
  type: "",
};

export const collectionTaskQueryCodec = defineQueryCodec<CollectionTaskSearch>({
  defaults: collectionTaskSearchDefaults,
  parse(params) {
    const statusRaw = params.get("status") ?? "ALL";
    const query = (params.get("q") ?? "").trim().slice(0, 200);
    const type = (params.get("type") ?? "").trim().slice(0, 100);
    const cursor = params.get("cursor");
    const drawerRaw = params.get("drawer");
    let drawer: CollectionTaskDrawerState | undefined;
    if (drawerRaw === "create") drawer = { mode: "create" };
    else if (drawerRaw?.startsWith("edit:")) {
      const taskId = drawerRaw.slice(5);
      if (stableIdentifier.test(taskId)) drawer = { mode: "edit", taskId };
    }
    return {
      status: statuses.has(statusRaw as CollectionTaskStatusFilter)
        ? (statusRaw as CollectionTaskStatusFilter)
        : "ALL",
      query,
      type,
      ...(cursor && cursor.length <= 16_384 ? { cursor } : {}),
      ...(drawer ? { drawer } : {}),
    };
  },
  build(value) {
    const params = new URLSearchParams();
    const status = value.status ?? "ALL";
    if (status !== "ALL") params.set("status", status);
    if (value.query) params.set("q", value.query);
    if (value.type) params.set("type", value.type);
    if (value.cursor) params.set("cursor", value.cursor);
    if (value.drawer?.mode === "create") params.set("drawer", "create");
    if (value.drawer?.mode === "edit") {
      params.set("drawer", `edit:${value.drawer.taskId}`);
    }
    return params;
  },
});
