import { describe, expect, it } from "vitest";
import { pageCatalog } from "./page-catalog";
import { safeReturnTo } from "../../shared/routing/route-registry";

describe("application page catalog", () => {
  it("discovers ingestion and account pages alongside all enabled business pages", () => {
    expect(pageCatalog.routes).toHaveLength(32);
    expect(pageCatalog.availability).toMatchObject({
      P02: true,
      P03: true,
      P04: true,
      P16: false,
      P17: false,
      ACCOUNT: true,
      ACCOUNT_SETTINGS: true,
    });
    expect(
      pageCatalog.routes.every((route) => typeof route.lazy === "function"),
    ).toBe(true);
  });

  it("registers only real enabled routes as return destinations", () => {
    for (const path of [
      "/collection-tasks",
      "/annotations/tag-review/task-1",
      "/manual/drafts/draft-1",
      "/account/settings",
    ]) {
      expect(safeReturnTo(`${path}?source=previous#details`)).toBe(
        `${path}?source=previous#details`,
      );
    }
    for (const path of [
      "/settings/calibrations",
      "/settings/data-schemas",
      "/annotation-tasks",
      "/collection-tasks/nonexistent",
      "//attacker.invalid/account",
    ]) {
      expect(safeReturnTo(path)).toBeNull();
    }
  });

  it("preserves the export, viewer and page-owned access boundaries", () => {
    const permissions = (path: string) =>
      pageCatalog.routes.find((route) => route.path === path)
        ?.requiredCapabilities;
    expect(permissions("/exports")).toEqual([
      "export.read",
      "dataset.read",
      "dataset_version.read",
    ]);
    expect(
      permissions(
        "/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view",
      ),
    ).toEqual(["episode.read"]);
    expect(permissions("/settings/access")).toEqual([]);
    expect(permissions("/manual/drafts/:draftId")).toEqual(["cleaning.read"]);
  });
});
