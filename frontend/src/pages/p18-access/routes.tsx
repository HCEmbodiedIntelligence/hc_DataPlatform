import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const routes: RouteObject[] = [
  { path: '/settings/access', lazy: () => import('./page') },
];

registerPageRoutes('P18', routes);
export default routes;
