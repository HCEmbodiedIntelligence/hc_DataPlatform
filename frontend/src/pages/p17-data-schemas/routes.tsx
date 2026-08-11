import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const routes: RouteObject[] = [
  { path: '/settings/data-schemas', lazy: () => import('./page') },
];

registerPageRoutes('P17', routes);
export default routes;
