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
  P12: true,
  P13: true,
  P14: true,
  P15: true,
  P16: true,
  P17: true,
  P18: true,
  P19: true,
  P20: true,
  P21: true,
  P22: true,
  P23: true,
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
    expect(upload?.activePatterns).toContain("/ingest/uploads/:uploadId");
    expect(
      items.find((item) => item.pageId === "P09")?.activePatterns,
    ).not.toContain("/ingest/uploads/:uploadId");
    expect(
      items.find((item) => item.pageId === "P09")?.activePatterns,
    ).toContain("/manual/issues/raw-diagnostic/:uploadId");
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

  it("keeps ordinary function entries visible without grants and hides administrator-only entries", () => {
    const visible = filterNavigationManifest(new Set<string>(), allPages);
    const items = visible.flatMap((group) => group.items);

    expect(items.map((item) => item.label)).toEqual([
      "工作台",
      "采集任务",
      "数据源",
      "数据上传",
      "录制切片",
      "数据集",
      "数据标注",
      "数据导出",
      "问题数据",
    ]);
    expect(items.every((item) => !item.administratorOnly)).toBe(true);
    expect(items.every((item) => !("role" in item))).toBe(true);
  });

  it("links the recording cutter to its list and deep workbench route", () => {
    const item = navigationManifest
      .flatMap((group) => group.items)
      .find((candidate) => candidate.pageId === "P23");

    expect(item).toMatchObject({
      label: "录制切片",
      path: "/recordings",
      requiredCapability: "episode.read",
    });
    expect(item?.activePatterns).toContain("/recordings/:recordingId/slice");
  });

  it("keeps schema authoring out of navigation for every role", () => {
    const administrator = filterNavigationManifest(
      new Set(["platform.admin", "data_schema.read"]),
      allPages,
    );
    const items = administrator.flatMap((group) => group.items);

    expect(items.some((item) => item.pageId === "P17")).toBe(false);
    expect(items.some((item) => item.path === "/settings/data-schemas")).toBe(
      false,
    );
  });

  it("keeps temporarily hidden pages out of navigation for every role", () => {
    const administrator = filterNavigationManifest(
      new Set(["platform.admin", "calibration.read"]),
      allPages,
    );
    const items = administrator.flatMap((group) => group.items);

    expect(items.some((item) => item.pageId === "P16")).toBe(false);
    expect(items.some((item) => item.path === "/settings/calibrations")).toBe(
      false,
    );
  });

  it("reveals administrator-only entries only when their administrator capability is granted", () => {
    const visible = filterNavigationManifest(
      new Set(["storage.overview.read", "audit.read"]),
      allPages,
    );
    const labels = visible.flatMap((group) =>
      group.items.map((item) => item.label),
    );

    expect(labels).toContain("存储容量");
    expect(labels).toContain("审计日志");
    expect(labels).not.toContain("生命周期");
    expect(labels).not.toContain("账户与权限");
  });

  it("exposes platform operations for either exact read or platform admin", () => {
    for (const capability of ["platform.operations.read", "platform.admin"]) {
      const items = filterNavigationManifest(
        new Set([capability]),
        allPages,
      ).flatMap((group) => group.items);
      expect(items.find((item) => item.pageId === "P22")).toMatchObject({
        label: "平台设置",
        path: "/settings/platform-operations",
        administratorOnly: true,
      });
    }
  });
});
