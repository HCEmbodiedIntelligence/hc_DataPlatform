import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

const routes: RouteObject[] = [{
  path: '/dashboard',
  lazy: async () => {
    const module = await import('./page');
    return { Component: module.DashboardPage };
  },
}];

registerPageRoutes('P01', routes);

export default routes;
