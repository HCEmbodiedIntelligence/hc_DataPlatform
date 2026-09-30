import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
import { annotationRoutes } from "../../features/annotation/routing";
export { annotationRoutes } from "../../features/annotation/routing";
export type PlatformRouteObject = PageRoute;

export const p08RouteRecords: PageRoute[] = [
  {
    path: annotationRoutes.annotate.pattern,
    requiredCapabilities: ["annotation_task.read"],
    navigation: {
      label: "数据标注",
      groupId: "production",
      order: 30,
      visibility: "discoverable",
    },
    lazy: async () => ({
      Component: (await import("./AnnotationQueuePage")).AnnotationQueuePage,
    }),
  },
  {
    path: annotationRoutes.revisions.pattern,
    requiredCapabilities: ["annotation_task.read"],
    lazy: async () => ({
      Component: (await import("./AnnotationRevisionPage"))
        .AnnotationRevisionPage,
    }),
  },
  {
    path: annotationRoutes.tagReview.pattern,
    requiredCapabilities: ["annotation_task.read"],
    lazy: async () => ({
      Component: (await import("./AnnotationQueuePage")).TagReviewQueuePage,
    }),
  },
  {
    path: annotationRoutes.tagReviewTask.pattern,
    requiredCapabilities: ["annotation_task.read", "episode.read"],
    lazy: async () => ({
      Component: (await import("./AnnotationTaskPage")).TagReviewTaskPage,
    }),
  },
  {
    path: annotationRoutes.task.pattern,
    requiredCapabilities: ["annotation_task.read", "episode.read"],
    lazy: async () => ({
      Component: (await import("./AnnotationTaskPage")).AnnotationTaskPage,
    }),
  },
];

export const page = definePage({
  id: "P08",
  enabled: true,
  routes: p08RouteRecords,
});
export default p08RouteRecords;
