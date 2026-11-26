import { definePage } from "../../shared/routing/page-descriptor";

export const page = definePage({
  id: "P30",
  enabled: true,
  routes: [
    {
      path: "/annotations/model",
      requiredCapabilities: ["annotation_task.read", "annotation.save"],
      navigation: {
        label: "模型标注",
        groupId: "production",
        order: 31,
        visibility: "authorized",
      },
      lazy: () => import("./page"),
    },
  ],
});
export default page.routes;
