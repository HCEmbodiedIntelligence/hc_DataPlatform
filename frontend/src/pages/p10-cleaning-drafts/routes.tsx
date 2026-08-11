import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

type CleaningDraftsRoute = RouteObject & {
  readonly navigationOwnerPageId: 'P10';
  readonly navigationOwnerGroupId: 'manual';
  readonly requiredCapabilities: readonly ['cleaning.read'];
};

export const routes: CleaningDraftsRoute[] = [{
  path: '/manual/drafts',
  navigationOwnerPageId: 'P10',
  navigationOwnerGroupId: 'manual',
  requiredCapabilities: ['cleaning.read'],
  lazy: async () => {
    const module = await import('./page');
    return { Component: module.CleaningDraftsPage };
  },
}];

registerPageRoutes('P10', routes);
export default routes;
