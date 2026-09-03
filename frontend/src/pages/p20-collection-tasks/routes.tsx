import type { RouteObject } from "react-router-dom";

type CollectionTaskRouteObject = RouteObject & {
  readonly requiredCapabilities: readonly string[];
};

export const collectionTaskRoutes = [
  {
    path: "/collection-tasks",
    requiredCapabilities: ["upload.read"],
    lazy: async () => ({
      Component: (await import("./page")).default,
    }),
  },
] satisfies readonly CollectionTaskRouteObject[];
