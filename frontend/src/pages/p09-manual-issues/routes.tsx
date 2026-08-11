import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';

type CleaningRouteObject = RouteObject & {
  readonly navigationOwnerPageId: 'P09';
  readonly navigationOwnerGroupId: 'manual';
  readonly requiredCapabilities: readonly ['manual_issue.read'];
};

export const routes: CleaningRouteObject[] = [{
  path: '/manual/issues',
  navigationOwnerPageId: 'P09',
  navigationOwnerGroupId: 'manual',
  requiredCapabilities: ['manual_issue.read'],
  lazy: async () => {
    const module = await import('./page');
    return { Component: module.ManualIssuesPage };
  },
}];

registerPageRoutes('P09', routes);
export default routes;
