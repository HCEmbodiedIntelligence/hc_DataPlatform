import type { AuthorizationSnapshot } from "../../shared/auth/contracts";
import {
  filterPageNavigation,
  type NavigationGroupId,
  type NavigationManifest,
  type PageAvailability,
} from "../../shared/routing/page-descriptor";
import { pageCatalog } from "../router/page-catalog";

export type {
  NavigationGroupId,
  NavigationItem,
  NavigationGroup,
  NavigationManifest,
  PageAvailability,
} from "../../shared/routing/page-descriptor";

export type NavigationCapabilitySource =
  | ReadonlySet<string>
  | Pick<AuthorizationSnapshot, "capabilities">;

export const navigationManifest = pageCatalog.navigation;

export function filterNavigationManifest(
  source: NavigationCapabilitySource,
  pageAvailability: PageAvailability,
): NavigationManifest {
  const capabilities =
    "capabilities" in source ? new Set(source.capabilities) : source;
  return filterPageNavigation(
    navigationManifest,
    capabilities,
    pageAvailability,
  );
}

export function resolveGroupLanding(
  groupId: NavigationGroupId,
  capabilities: NavigationCapabilitySource,
  pageAvailability: PageAvailability,
): string | null {
  return (
    filterNavigationManifest(capabilities, pageAvailability).find(
      (group) => group.groupId === groupId,
    )?.items[0]?.path ?? null
  );
}
