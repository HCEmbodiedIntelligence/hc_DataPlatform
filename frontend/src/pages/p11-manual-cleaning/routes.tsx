import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

type CleaningWorkbenchRoute = RouteObject & {
  readonly navigationOwnerPageId: 'P10';
  readonly navigationOwnerGroupId: 'manual';
  readonly requiredCapabilities: readonly ['cleaning.read'];
  readonly hiddenFromNavigation: true;
};

export const routes: CleaningWorkbenchRoute[] = [{
  path: '/manual/drafts/:draftId',
  navigationOwnerPageId: 'P10',
  navigationOwnerGroupId: 'manual',
  requiredCapabilities: ['cleaning.read'],
  hiddenFromNavigation: true,
  lazy: async () => {
    const module = await import('./page');
    return { Component: module.ManualCleaningWorkbenchPage };
  },
}];

registerPageRoutes('P11', routes);
export default routes;
