import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
import { dataUploadRoutes } from "../../shared/routing/navigation-routes";

export const routes: PageRoute[] = [
  {
    path: dataUploadRoutes.newUpload,
    requiredCapabilities: ["upload.read"],
    navigation: {
      label: "数据上传",
      groupId: "ingest",
      order: 30,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
  {
    path: dataUploadRoutes.records,
    requiredCapabilities: ["upload.read"],
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
];

export const page = definePage({ id: "P03", enabled: true, routes: routes });
export default routes;
