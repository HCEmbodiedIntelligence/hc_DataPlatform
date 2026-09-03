import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export type DatasetRouteObject = RouteObject & {
  readonly navigationOwnerPageId: 'P06';
  readonly navigationOwnerGroupId: 'datasets';
  readonly hiddenFromNavigation?: boolean;
  readonly requiredCapabilities: readonly string[];
};

export const p06Routes: DatasetRouteObject[] = [
  {
    path: '/datasets/:datasetId',
    navigationOwnerPageId: 'P06',
    navigationOwnerGroupId: 'datasets',
    requiredCapabilities: ['dataset.read'],
    lazy: async () => ({ Component: (await import('./page')).DatasetDetailPage }),
  },
  {
    path: '/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view',
    navigationOwnerPageId: 'P06',
    navigationOwnerGroupId: 'datasets',
    hiddenFromNavigation: true,
    requiredCapabilities: ['episode.read'],
    lazy: async () => ({ Component: (await import('./ViewerShell')).EpisodeViewerShell }),
  },
];

registerPageRoutes('P06', p06Routes);
export { registerPageRoutes };
export default p06Routes;
