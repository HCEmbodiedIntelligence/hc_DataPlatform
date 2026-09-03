import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

export const p05Routes: RouteObject[] = [
  { path: '/datasets', lazy: async () => ({ Component: (await import('./page')).DatasetsPage }) },
];

registerPageRoutes('P05', p05Routes);
export { registerPageRoutes };
export default p05Routes;
