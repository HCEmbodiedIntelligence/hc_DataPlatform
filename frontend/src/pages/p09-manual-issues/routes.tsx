import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type CleaningRouteObject = PageRoute;

export const routes: PageRoute[] = [
  {
    path: "/manual/issues",
    requiredCapabilities: ["manual_issue.read"],
    navigation: {
      label: "问题数据",
      groupId: "production",
      order: 50,
      visibility: "discoverable",
    },
    lazy: async () => ({
      Component: (await import("./page")).ManualIssuesPage,
    }),
  },
  {
    path: "/manual/issues/raw-diagnostic/:uploadId",
    requiredCapabilities: ["manual_issue.read", "upload.read"],
    lazy: async () => ({
      Component: (await import("./raw-diagnostic-route")).default,
    }),
  },
];

export const page = definePage({ id: "P09", enabled: true, routes: routes });
export default routes;
