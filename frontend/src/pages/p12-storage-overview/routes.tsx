import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

const routes: RouteObject[] = [{
  path: '/storage/overview',
  lazy: async () => {
    const module = await import('./page');
    return { Component: module.StorageOverviewPage };
  },
}];

registerPageRoutes('P12', routes);
export default routes;
