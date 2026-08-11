import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const routes: RouteObject[] = [
  { path: '/settings/robot-models', lazy: () => import('./page') },
];
registerPageRoutes('P14', routes);
export default routes;

