import {
  buildPageCatalog,
  type PageDescriptor,
} from "../../shared/routing/page-descriptor";
import { replaceRegisteredPageRoutes } from "../../shared/routing/route-registry";

const modules = import.meta.glob<{ page: PageDescriptor }>(
  ["/src/pages/*/routes.tsx", "!/src/pages/auth/routes.tsx"],
  { eager: true },
);

export const pageCatalog = buildPageCatalog(
  Object.entries(modules).map(([path, module]) => {
    if (!module.page) throw new Error(`Missing page descriptor: ${path}`);
    return module.page;
  }),
);

replaceRegisteredPageRoutes(
  pageCatalog.pages
    .filter((page) => page.enabled)
    .map((page) => ({ pageId: page.id, routes: page.routes })),
);
