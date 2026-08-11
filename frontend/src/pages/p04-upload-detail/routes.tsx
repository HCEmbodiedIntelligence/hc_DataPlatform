import type { RouteObject } from 'react-router-dom';
import { registerPageRoutes } from '../../shared/routing/route-registry';
import UploadDetailPage from './page';

type IngestRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: 'ingest';
  readonly navigationOwnerPageId: 'P03';
  readonly requiredCapabilities: readonly string[];
  readonly hiddenFromNavigation: true;
};

const pageRoutes: readonly IngestRouteObject[] = [{
  path: '/ingest/uploads/:uploadId',
  element: <UploadDetailPage />,
  navigationOwnerGroupId: 'ingest',
  navigationOwnerPageId: 'P03',
  requiredCapabilities: ['upload.read'],
  hiddenFromNavigation: true,
}];
registerPageRoutes('P04', pageRoutes);
export default pageRoutes;
