import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type CleaningDraftsRoute = PageRoute;

export const routes: PageRoute[] = [
  {
    path: "/manual/drafts",
    requiredCapabilities: ["cleaning.read"],
    navigationOwnerPageId: "P08",
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
];

export const page = definePage({ id: "P10", enabled: true, routes: routes });
export default routes;
