import type { RouteObject } from 'react-router-dom';
import { annotationQueueQueryCodec, annotationTaskQueryCodec } from './query-codec';
import type { AnnotationQueueSearch, AnnotationTaskSearch } from './query-codec';

export type PlatformRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: 'annotation';
  readonly navigationOwnerPageId: 'P08';
  readonly requiredCapabilities: readonly string[];
  readonly hiddenFromNavigation?: boolean;
};

function suffix(query: string): string { return query ? `?${query}` : ''; }

export const annotationRoutes = {
  queue: {
    pattern: '/annotations',
    build(search?: AnnotationQueueSearch) { return `/annotations${suffix(search ? annotationQueueQueryCodec.build(search) : '')}`; },
  },
  task: {
    pattern: '/annotations/tasks/:taskId',
    build(params: { taskId: string }, search?: AnnotationTaskSearch) {
      return `/annotations/tasks/${encodeURIComponent(params.taskId)}${suffix(search ? annotationTaskQueryCodec.build(search) : '')}`;
    },
  },
} as const;

export const p08RouteRecords: readonly PlatformRouteObject[] = [
  {
    path: annotationRoutes.queue.pattern,
    navigationOwnerGroupId: 'annotation',
    navigationOwnerPageId: 'P08',
    requiredCapabilities: ['annotation_task.read'],
    lazy: async () => ({ Component: (await import('./AnnotationQueuePage')).AnnotationQueuePage }),
  },
  {
    path: annotationRoutes.task.pattern,
    navigationOwnerGroupId: 'annotation',
    navigationOwnerPageId: 'P08',
    requiredCapabilities: ['annotation_task.read', 'episode.read'],
    hiddenFromNavigation: true,
    lazy: async () => ({ Component: (await import('./AnnotationTaskPage')).AnnotationTaskPage }),
  },
];
