import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const routes: RouteObject[] = [
  { path: '/storage/lifecycle', lazy: () => import('./page') },
];

registerPageRoutes('P13', routes);
export default routes;

