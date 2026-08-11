import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';
import UploadJobsPage from './page';

type IngestRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: 'ingest';
  readonly navigationOwnerPageId: 'P03';
  readonly requiredCapabilities: readonly string[];
  readonly defaultGroupLanding: true;
};

const pageRoutes: readonly IngestRouteObject[] = [{
  path: '/ingest/uploads',
  element: <UploadJobsPage />,
  navigationOwnerGroupId: 'ingest',
  navigationOwnerPageId: 'P03',
  requiredCapabilities: ['upload.read'],
  defaultGroupLanding: true,
}];
registerPageRoutes('P03', pageRoutes);
export default pageRoutes;
