import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const p07Routes: RouteObject[] = [
  {
    path: '/datasets/:datasetId/versions/:versionId',
    lazy: async () => ({ Component: (await import('./page')).VersionDetailPage }),
  },
];

registerPageRoutes('P07', p07Routes);
export { registerPageRoutes };
export default p07Routes;
