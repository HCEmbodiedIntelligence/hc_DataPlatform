import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type RecordingSegmentationRoute = PageRoute;

export const recordingSegmentationRoutes: PageRoute[] = [
  {
    path: "/recordings",
    requiredCapabilities: ["episode.read"],
    navigation: {
      label: "录制切片",
      groupId: "production",
      order: 10,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
  {
    path: "/recordings/:recordingId/slice",
    requiredCapabilities: ["episode.read"],
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
];

export const page = definePage({
  id: "P23",
  enabled: true,
  routes: recordingSegmentationRoutes,
});
export default recordingSegmentationRoutes;
