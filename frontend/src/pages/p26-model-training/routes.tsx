import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

const routes: PageRoute[] = [
  {
    path: "/model-training",
    requiredCapabilities: ["training.read"],
    navigation: {
      label: "模型训练",
      groupId: "production",
      order: 60,
      visibility: "discoverable",
    },
    lazy: () => import("./page"),
  },
];

export const page = definePage({ id: "P26", enabled: true, routes });
export default routes;
