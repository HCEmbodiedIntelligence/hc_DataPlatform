import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

const routes: RouteObject[] = [{
  path: '/settings/audit',
  lazy: async () => {
    const module = await import('./page');
    return { Component: module.AuditPage };
  },
}];

registerPageRoutes('P19', routes);
export default routes;
