import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const routes: RouteObject[] = [
  { path: '/settings/calibrations', lazy: () => import('./page') },
];

registerPageRoutes('P16', routes);
export default routes;
