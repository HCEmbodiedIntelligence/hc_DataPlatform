import type { RouteObject } from "react-router-dom";
import {
  annotationQueueQueryCodec,
  annotationTaskQueryCodec,
} from "./query-codec";
import type {
  AnnotationQueueSearch,
  AnnotationTaskSearch,
} from "./query-codec";
import { dataAnnotationRoutes } from "../../app/shell/navigation-routes";

export type PlatformRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: "annotation";
  readonly navigationOwnerPageId: "P08";
  readonly requiredCapabilities: readonly string[];
  readonly hiddenFromNavigation?: boolean;
};

function suffix(query: string): string {
  return query ? `?${query}` : "";
}

export const annotationRoutes = {
  annotate: {
    pattern: dataAnnotationRoutes.annotate,
    build(search?: AnnotationQueueSearch) {
      return `${dataAnnotationRoutes.annotate}${suffix(search ? annotationQueueQueryCodec.build(search) : "")}`;
    },
  },
  revisions: {
    pattern: dataAnnotationRoutes.revisions,
    build() {
      return dataAnnotationRoutes.revisions;
    },
  },
  tagReview: {
    pattern: dataAnnotationRoutes.tagReview,
    build(search?: AnnotationQueueSearch) {
      return `${dataAnnotationRoutes.tagReview}${suffix(search ? annotationQueueQueryCodec.build(search) : "")}`;
    },
  },
  tagReviewTask: {
    pattern: `${dataAnnotationRoutes.tagReview}/:taskId`,
    build(params: { taskId: string }) {
      return `${dataAnnotationRoutes.tagReview}/${encodeURIComponent(params.taskId)}`;
    },
  },
  task: {
    pattern: "/annotations/tasks/:taskId",
    build(params: { taskId: string }, search?: AnnotationTaskSearch) {
      return `/annotations/tasks/${encodeURIComponent(params.taskId)}${suffix(search ? annotationTaskQueryCodec.build(search) : "")}`;
    },
  },
} as const;

export const p08RouteRecords: readonly PlatformRouteObject[] = [
  {
    path: annotationRoutes.annotate.pattern,
    navigationOwnerGroupId: "annotation",
    navigationOwnerPageId: "P08",
    requiredCapabilities: ["annotation_task.read"],
    lazy: async () => ({
      Component: (await import("./AnnotationQueuePage")).AnnotationQueuePage,
    }),
  },
  {
    path: dataAnnotationRoutes.revisions,
    navigationOwnerGroupId: "annotation",
    navigationOwnerPageId: "P08",
    requiredCapabilities: ["annotation_task.read"],
    lazy: async () => ({
      Component: (await import("./AnnotationRevisionPage"))
        .AnnotationRevisionPage,
    }),
  },
  {
    path: dataAnnotationRoutes.tagReview,
    navigationOwnerGroupId: "annotation",
    navigationOwnerPageId: "P08",
    requiredCapabilities: ["annotation_task.read"],
    hiddenFromNavigation: true,
    lazy: async () => ({
      Component: (await import("./AnnotationQueuePage")).TagReviewQueuePage,
    }),
  },
  {
    path: annotationRoutes.tagReviewTask.pattern,
    navigationOwnerGroupId: "annotation",
    navigationOwnerPageId: "P08",
    requiredCapabilities: ["annotation_task.read", "episode.read"],
    hiddenFromNavigation: true,
    lazy: async () => ({
      Component: (await import("./AnnotationTaskPage")).TagReviewTaskPage,
    }),
  },
  {
    path: annotationRoutes.task.pattern,
    navigationOwnerGroupId: "annotation",
    navigationOwnerPageId: "P08",
    requiredCapabilities: ["annotation_task.read", "episode.read"],
    hiddenFromNavigation: true,
    lazy: async () => ({
      Component: (await import("./AnnotationTaskPage")).AnnotationTaskPage,
    }),
  },
];
