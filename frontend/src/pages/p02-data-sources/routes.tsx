import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';
import DataSourcesPage from './page';

type IngestRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: 'ingest';
  readonly navigationOwnerPageId: 'P02';
  readonly requiredCapabilities: readonly string[];
};

const pageRoutes: readonly IngestRouteObject[] = [{
  path: '/ingest/sources',
  element: <DataSourcesPage />,
  navigationOwnerGroupId: 'ingest',
  navigationOwnerPageId: 'P02',
  requiredCapabilities: ['ingest_source.read'],
}];
registerPageRoutes('P02', pageRoutes);
export default pageRoutes;
