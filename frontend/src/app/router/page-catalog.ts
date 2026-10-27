import {
  buildPageCatalog,
  type PageDescriptor,
} from "../../shared/routing/page-descriptor";
import { replaceRegisteredPageRoutes } from "../../shared/routing/route-registry";
import { authRoutes } from "../../pages/auth/routes";

const modules = import.meta.glob<{ page: PageDescriptor }>(
  ["/src/pages/*/routes.tsx", "!/src/pages/auth/routes.tsx"],
  { eager: true },
);

export const pageCatalog = buildPageCatalog(
  Object.entries(modules).map(([path, module]) => {
    if (!module.page) throw new Error(`Missing page descriptor: ${path}`);
    return module.page;
  }),
  {
    reservedPaths: [
      "/",
      ...authRoutes.flatMap((route) => (route.path ? [route.path] : [])),
    ],
  },
);

const pendingPreloads = new Map<string, Promise<unknown>>();

/** Warm code on navigation intent; never fetch private page data or run loaders. */
export function preloadNavigationPage(path: string): void {
  const connection = (navigator as Navigator & {
    connection?: { saveData?: boolean; effectiveType?: string };
  }).connection;
  if (connection?.saveData || /(^|-)2g$/u.test(connection?.effectiveType ?? "")) return;
  if (pendingPreloads.has(path) || pendingPreloads.size >= 2) return;
  const route = pageCatalog.routes.find((candidate) => candidate.path === path);
  if (!route || typeof route.lazy !== "function") return;
  const load = Promise.resolve(route.lazy());
  pendingPreloads.set(path, load);
  // Dynamic import itself retains successful modules. Failed preloads must not
  // poison a later click; the router owns the visible error/retry behavior.
  void load.catch(() => undefined).finally(() => pendingPreloads.delete(path));
}

replaceRegisteredPageRoutes(
  pageCatalog.pages
    .filter((page) => page.enabled)
    .map((page) => ({ pageId: page.id, routes: page.routes })),
);
