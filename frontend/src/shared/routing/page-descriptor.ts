import type { RouteObject } from "react-router-dom";
import {
  isRegisteredCapability,
  type RegisteredCapability,
} from "../auth/capabilities";

export const navigationGroups = [
  { groupId: "dashboard", label: "工作台" },
  { groupId: "ingest", label: "采集与接收" },
  { groupId: "production", label: "数据生产" },
  { groupId: "governance", label: "治理与资产" },
  { groupId: "security", label: "安全与审计" },
] as const;

export type NavigationGroupId = (typeof navigationGroups)[number]["groupId"];

export interface PageNavigation {
  label: string;
  groupId: NavigationGroupId;
  order: number;
  // Discoverability is intentionally independent of authorization. Ordinary
  // entries remain visible for personal accounts; the route still checks grants.
  visibility: "discoverable" | "authorized";
  // Only for a page-owned authorization boundary such as P18's two query families.
  capability?: RegisteredCapability;
}

export type PageRoute = RouteObject & {
  path: string;
  lazy: NonNullable<RouteObject["lazy"]>;
  requiredCapabilities: readonly RegisteredCapability[];
  navigation?: PageNavigation;
  navigationOwnerPageId?: string;
};

export interface PageDescriptor {
  id: string;
  enabled: boolean;
  routes: readonly PageRoute[];
}

export interface NavigationItem {
  pageId: string;
  label: string;
  path: string;
  requiredCapability: RegisteredCapability | null;
  requiredCapabilities: readonly RegisteredCapability[];
  administratorOnly: boolean;
  activePatterns: readonly string[];
}

export interface NavigationGroup {
  groupId: NavigationGroupId;
  label: string;
  items: readonly NavigationItem[];
}

export type NavigationManifest = readonly NavigationGroup[];
export type PageAvailability = Readonly<Record<string, boolean>>;

export interface PageCatalog {
  pages: readonly PageDescriptor[];
  routes: readonly PageRoute[];
  navigation: NavigationManifest;
  availability: PageAvailability;
}

export function definePage(page: PageDescriptor): PageDescriptor {
  if (!page.id || typeof page.enabled !== "boolean") {
    throw new Error("Pages must declare an id and enabled state");
  }
  for (const route of page.routes) {
    if (
      !route.path?.startsWith("/") ||
      route.path.startsWith("//") ||
      route.children ||
      route.index ||
      !route.lazy ||
      !Array.isArray(route.requiredCapabilities) ||
      !route.requiredCapabilities.every(isRegisteredCapability)
    ) {
      throw new Error(`Invalid route contract: ${page.id} ${route.path}`);
    }
    const navigation = route.navigation;
    if (navigation) {
      if (
        !navigationGroups.some(
          (group) => group.groupId === navigation.groupId,
        ) ||
        !["discoverable", "authorized"].includes(navigation.visibility) ||
        !Number.isFinite(navigation.order) ||
        /[:*]/u.test(route.path) ||
        (navigation.capability !== undefined &&
          !isRegisteredCapability(navigation.capability)) ||
        (navigation.visibility === "authorized" &&
          route.requiredCapabilities.length === 0 &&
          !navigation.capability)
      ) {
        throw new Error(`Invalid navigation contract: ${page.id}`);
      }
    }
  }
  return Object.freeze({
    ...page,
    routes: Object.freeze(
      page.routes.map((route) =>
        Object.freeze({
          ...route,
          requiredCapabilities: Object.freeze([...route.requiredCapabilities]),
          ...(route.navigation
            ? { navigation: Object.freeze({ ...route.navigation }) }
            : {}),
        }),
      ),
    ),
  });
}

export function buildPageCatalog(
  descriptors: readonly PageDescriptor[],
): PageCatalog {
  const ids = new Set<string>();
  const paths = new Set<string>();
  const pages = descriptors.map(definePage);
  const enabled = pages.filter((page) => page.enabled);
  const routes: PageRoute[] = [];
  const availability: Record<string, boolean> = {};
  const entries: {
    groupId: NavigationGroupId;
    order: number;
    item: NavigationItem;
  }[] = [];
  for (const page of pages) {
    if (ids.has(page.id)) throw new Error(`Duplicate page id: ${page.id}`);
    ids.add(page.id);
    availability[page.id] = page.enabled && page.routes.length > 0;
    if (!page.enabled) continue;
    for (const route of page.routes) {
      // React Router matches paths case-insensitively by default. Parameter names
      // and trailing slashes cannot distinguish two routes either.
      const key = route.path
        .replace(/:[^/]+/gu, ":param")
        .replace(/\/+$/u, "")
        .toLowerCase();
      if (paths.has(key)) throw new Error(`Duplicate page path: ${route.path}`);
      paths.add(key);
      routes.push(route);
      if (!route.navigation) continue;
      if (entries.some((entry) => entry.item.pageId === page.id)) {
        throw new Error(`Multiple navigation entries for page: ${page.id}`);
      }
      const navigation = route.navigation;
      const requiredCapabilities = navigation.capability
        ? [navigation.capability]
        : route.requiredCapabilities;
      entries.push({
        groupId: navigation.groupId,
        order: navigation.order,
        item: {
          pageId: page.id,
          label: navigation.label,
          path: route.path,
          requiredCapability: requiredCapabilities[0] ?? null,
          requiredCapabilities,
          administratorOnly: navigation.visibility === "authorized",
          activePatterns: enabled.flatMap((owner) =>
            owner.routes
              .filter(
                (candidate) =>
                  (candidate.navigationOwnerPageId ?? owner.id) === page.id,
              )
              .map((candidate) => candidate.path),
          ),
        },
      });
    }
  }
  for (const route of routes) {
    if (
      route.navigationOwnerPageId &&
      !entries.some(
        (entry) => entry.item.pageId === route.navigationOwnerPageId,
      )
    ) {
      throw new Error(
        `Missing navigation owner: ${route.navigationOwnerPageId}`,
      );
    }
  }
  return {
    pages,
    routes: Object.freeze(routes),
    availability: Object.freeze(availability),
    navigation: navigationGroups.flatMap((group) => {
      const items = entries
        .filter((entry) => entry.groupId === group.groupId)
        .sort(
          (left, right) =>
            left.order - right.order ||
            left.item.pageId.localeCompare(right.item.pageId),
        )
        .map((entry) => entry.item);
      return items.length ? [{ ...group, items }] : [];
    }),
  };
}

export function filterPageNavigation(
  manifest: NavigationManifest,
  capabilities: ReadonlySet<string>,
  availability: PageAvailability,
): NavigationManifest {
  return manifest.flatMap((group) => {
    const items = group.items.filter(
      (item) =>
        availability[item.pageId] === true &&
        (!item.administratorOnly ||
          capabilities.has("platform.admin") ||
          item.requiredCapabilities.every(
            (capability) =>
              isRegisteredCapability(capability) &&
              capabilities.has(capability),
          )),
    );
    return items.length ? [{ ...group, items }] : [];
  });
}
