import { describe, expect, it } from "vitest";
import {
  filterNavigationManifest,
  navigationManifest,
  type PageAvailability,
} from "./navigation-manifest";
import { dataAnnotationRoutes, dataUploadRoutes } from "./navigation-routes";

const allPages: PageAvailability = Object.freeze({
  P01: true,
  P02: true,
  P03: true,
  P05: true,
  P08: true,
  P09: true,
  P10: true,
  P11: true,
});

describe("navigation manifest", () => {
  it("exposes function entries without independent upload-record or cleaning navigation", () => {
    const items = navigationManifest.flatMap((group) => group.items);
    const upload = items.find((item) => item.pageId === "P03");
    const annotation = items.find((item) => item.pageId === "P08");

    expect(upload).toMatchObject({
      label: "数据上传",
      path: dataUploadRoutes.newUpload,
    });
    expect(annotation).toMatchObject({
      label: "数据标注",
      path: dataAnnotationRoutes.annotate,
    });
    expect(annotation?.activePatterns).toEqual(
      expect.arrayContaining([
        dataAnnotationRoutes.annotate,
        dataAnnotationRoutes.revisions,
        dataAnnotationRoutes.tagReview,
      ]),
    );
    expect(
      items.some((item) =>
        ["上传记录", "数据清洗", "清洗草稿"].includes(item.label),
      ),
    ).toBe(false);
    expect(
      navigationManifest.map((group) => String(group.groupId)),
    ).not.toContain("manual");
  });

  it("uses capabilities only to filter shared function entries", () => {
    const visible = filterNavigationManifest(
      new Set(["upload.read", "annotation_task.read"]),
      allPages,
    );
    const items = visible.flatMap((group) => group.items);

    expect(items.map((item) => item.label)).toEqual([
      "工作台",
      "数据上传",
      "数据标注",
    ]);
    expect(items.every((item) => !("role" in item))).toBe(true);
  });
});
