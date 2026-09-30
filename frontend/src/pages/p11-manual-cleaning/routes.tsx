import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type CleaningWorkbenchRoute = PageRoute;

export const routes: PageRoute[] = [
  {
    path: "/manual/drafts/:draftId",
    requiredCapabilities: ["cleaning.read"],
    navigationOwnerPageId: "P08",
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
];

export const page = definePage({ id: "P11", enabled: true, routes: routes });
export default routes;
