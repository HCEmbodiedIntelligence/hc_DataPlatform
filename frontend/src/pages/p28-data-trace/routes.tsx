import { definePage } from "../../shared/routing/page-descriptor";

export const page = definePage({
  id: "P28",
  enabled: true,
  routes: [
    {
      path: "/data-trace",
      requiredCapabilities: [
        "dataset_version.read",
        "episode.read",
        "upload.read",
      ],
      navigation: {
        label: "数据回溯",
        groupId: "production",
        order: 51,
        visibility: "authorized",
      },
      lazy: () => import("./page"),
    },
  ],
});
