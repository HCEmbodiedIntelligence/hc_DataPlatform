import {
  annotationQueueQueryCodec,
  annotationTaskQueryCodec,
  type AnnotationQueueSearch,
  type AnnotationTaskSearch,
} from "./query-codec";
import { dataAnnotationRoutes } from "../../shared/routing/navigation-routes";

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
