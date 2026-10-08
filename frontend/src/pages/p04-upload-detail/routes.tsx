import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/ingest/uploads/:uploadId",
    requiredCapabilities: ["upload.read"],
    navigationOwnerPageId: "P03",
    lazy: async () => ({ Component: (await import("./formal-page")).default }),
  },
];

export const page = definePage({ id: "P04", enabled: true, routes: routes });
export default routes;
