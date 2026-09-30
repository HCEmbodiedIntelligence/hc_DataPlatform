import { describe, expect, it } from "vitest";
import {
  buildPageCatalog,
  definePage,
  filterPageNavigation,
  type PageDescriptor,
} from "./page-descriptor";

const page = definePage({
  id: "CUSTOM",
  enabled: true,
  routes: [
    {
      path: "/custom-datasets",
      requiredCapabilities: ["dataset.read"],
      navigation: {
        label: "自定义数据",
        groupId: "production",
        order: 1,
        visibility: "authorized",
      },
      lazy: async () => ({ Component: () => null }),
    },
  ],
});

describe("page descriptors", () => {
  it("derives navigation and access from one route and keeps availability isolated", () => {
    const catalog = buildPageCatalog([page]);
    const item = catalog.navigation[0]?.items[0];
    expect(item).toMatchObject({
      pageId: page.id,
      path: "/custom-datasets",
      requiredCapability: "dataset.read",
      activePatterns: ["/custom-datasets"],
    });
    expect(
      filterPageNavigation(catalog.navigation, new Set(), catalog.availability),
    ).toEqual([]);
    expect(
      filterPageNavigation(
        catalog.navigation,
        new Set(["dataset.read"]),
        catalog.availability,
      )[0]?.items,
    ).toEqual([item]);
    expect(
      filterPageNavigation(catalog.navigation, new Set(["dataset.read"]), {}),
    ).toEqual([]);
    expect(buildPageCatalog([]).routes).toEqual([]);
  });

  it("removes disabled pages from routes, navigation and availability", () => {
    const catalog = buildPageCatalog([{ ...page, enabled: false }]);
    expect(catalog.routes).toEqual([]);
    expect(catalog.navigation).toEqual([]);
    expect(catalog.availability.CUSTOM).toBe(false);
  });

  it("rejects duplicate ids, ambiguous paths and missing navigation owners", () => {
    expect(() => buildPageCatalog([page, page])).toThrow("Duplicate page id");
    expect(() => buildPageCatalog([page, { ...page, id: "OTHER" }])).toThrow(
      "Duplicate page path",
    );
    const variants = ["/ITEMS/:first/", "/items/:second"].map((path, i) => ({
      ...page,
      id: `VARIANT${i}`,
      routes: [{ ...page.routes[0]!, path, navigation: undefined }],
    }));
    expect(() => buildPageCatalog(variants)).toThrow("Duplicate page path");
    expect(() =>
      buildPageCatalog([
        {
          ...page,
          routes: [{ ...page.routes[0]!, navigationOwnerPageId: "MISSING" }],
        },
      ]),
    ).toThrow("Missing navigation owner");
  });

  it.each([undefined, ["not.registered"], ["platform.operations.typo"]])(
    "fails closed on missing or unknown permission %j",
    (requiredCapabilities) => {
      const invalid = {
        ...page,
        routes: [{ ...page.routes[0], requiredCapabilities }],
      };
      expect(() =>
        buildPageCatalog([invalid as unknown as PageDescriptor]),
      ).toThrow("Invalid route contract");
    },
  );

  it("requires lazy page loading and a navigable static landing URL", () => {
    expect(() =>
      definePage({
        ...page,
        routes: [{ ...page.routes[0], lazy: undefined }],
      } as unknown as PageDescriptor),
    ).toThrow("Invalid route contract");
    expect(() =>
      definePage({
        ...page,
        routes: [{ ...page.routes[0]!, path: "/custom/:id" }],
      }),
    ).toThrow("Invalid navigation contract");
  });
});
