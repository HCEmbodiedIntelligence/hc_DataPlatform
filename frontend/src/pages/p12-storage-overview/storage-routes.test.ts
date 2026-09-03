import { describe, expect, it } from "vitest";
import { navigationManifest } from "../../app/shell/navigation-manifest";
import { LifecyclePage } from "../p13-storage-lifecycle/page";
import lifecycleRoutes from "../p13-storage-lifecycle/routes";
import { StorageOverviewPage } from "./page";
import capacityRoutes from "./routes";

describe("storage capacity and lifecycle routes", () => {
  it("registers distinct URLs that load distinct page components", async () => {
    const capacityRoute = capacityRoutes[0];
    const lifecycleRoute = lifecycleRoutes[0];

    expect(capacityRoute?.path).toBe("/storage/overview");
    expect(lifecycleRoute?.path).toBe("/storage/lifecycle");
    if (typeof capacityRoute?.lazy !== "function")
      throw new Error("P12 route must be lazy");
    if (typeof lifecycleRoute?.lazy !== "function")
      throw new Error("P13 route must be lazy");

    const [capacityModule, lifecycleModule] = await Promise.all([
      capacityRoute.lazy(),
      lifecycleRoute.lazy(),
    ]);

    expect(capacityModule.Component).toBe(StorageOverviewPage);
    expect(lifecycleModule.Component).toBe(LifecyclePage);
    expect(capacityModule.Component).not.toBe(lifecycleModule.Component);
  });

  it("keeps the two navigation entries route-exclusive", () => {
    const items = navigationManifest.flatMap((group) => group.items);
    const capacityItem = items.find((item) => item.pageId === "P12");
    const lifecycleItem = items.find((item) => item.pageId === "P13");

    expect(capacityItem).toMatchObject({
      label: "存储容量",
      path: "/storage/overview",
      activePatterns: ["/storage/overview"],
    });
    expect(lifecycleItem).toMatchObject({
      label: "生命周期",
      path: "/storage/lifecycle",
      activePatterns: ["/storage/lifecycle"],
    });
  });
});
