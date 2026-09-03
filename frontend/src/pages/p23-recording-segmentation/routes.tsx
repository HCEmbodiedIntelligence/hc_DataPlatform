import type { RouteObject } from "react-router-dom";

type RecordingSegmentationRoute = RouteObject & {
  readonly requiredCapabilities: readonly string[];
};

export const recordingSegmentationRoutes = [
  {
    path: "/recordings",
    requiredCapabilities: ["episode.read"],
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
  {
    path: "/recordings/:recordingId/slice",
    requiredCapabilities: ["episode.read"],
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
] satisfies readonly RecordingSegmentationRoute[];

export default recordingSegmentationRoutes;
