import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";
import {
  LegacyUploadIndexRedirect,
} from "../../app/shell/RouteCompatibility";
import { dataUploadRoutes } from "../../app/shell/navigation-routes";
import UploadJobsPage from "./page";

type IngestRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: "ingest";
  readonly navigationOwnerPageId: "P03";
  readonly requiredCapabilities: readonly string[];
  readonly hiddenFromNavigation?: boolean;
  readonly defaultGroupLanding?: true;
};

const pageRoutes: readonly IngestRouteObject[] = [
  {
    path: dataUploadRoutes.legacyIndex,
    element: <LegacyUploadIndexRedirect />,
    navigationOwnerGroupId: "ingest",
    navigationOwnerPageId: "P03",
    requiredCapabilities: ["upload.read"],
    hiddenFromNavigation: true,
  },
  {
    path: dataUploadRoutes.newUpload,
    element: <UploadJobsPage />,
    navigationOwnerGroupId: "ingest",
    navigationOwnerPageId: "P03",
    requiredCapabilities: ["upload.read"],
    defaultGroupLanding: true,
  },
  {
    path: dataUploadRoutes.records,
    element: <UploadJobsPage />,
    navigationOwnerGroupId: "ingest",
    navigationOwnerPageId: "P03",
    requiredCapabilities: ["upload.read"],
    hiddenFromNavigation: true,
  },
];
registerPageRoutes("P03", pageRoutes);
export default pageRoutes;
