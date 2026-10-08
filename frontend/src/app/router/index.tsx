import {
  Navigate,
  createBrowserRouter,
  type RouteObject,
} from "react-router-dom";
import { pageCatalog } from "./page-catalog";
import { guardedPageRoutes } from "./page-routes";
import { RuntimePlatformShell } from "../shell/RuntimePlatformShell";
import { authRoutes } from "../../pages/auth/routes";
import { expandGrantedCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { filterNavigationManifest } from "../shell/navigation-manifest";

export const aggregatedPageRoutes: readonly RouteObject[] = pageCatalog.routes;
export const pageAvailability = pageCatalog.availability;

function RootLanding() {
  const bootstrapLoaded = useShellStore((state) => state.bootstrapLoaded);
  const authorizationFailed = useShellStore(
    (state) => state.authorizationFailed,
  );
  const authorization = useShellStore((state) => state.authorization);
  const platformCapabilities = useShellStore(
    (state) => state.platformCapabilities,
  );
  if (!bootstrapLoaded && !authorizationFailed) return null;
  const capabilities = expandGrantedCapabilities([
    ...(authorization?.capabilities ?? []),
    ...platformCapabilities,
  ]);
  const legalPath =
    filterNavigationManifest(capabilities, pageAvailability)[0]?.items[0]
      ?.path ?? "/dashboard";
  return <Navigate replace to={legalPath} />;
}

export function createPlatformRouter() {
  return createBrowserRouter([
    ...authRoutes,
    {
      path: "/",
      element: <RuntimePlatformShell pageAvailability={pageAvailability} />,
      children: [
        { index: true, element: <RootLanding /> },
        ...guardedPageRoutes(pageCatalog),
        {
          path: "*",
          element: (
            <section role="alert">
              <h1>页面不存在或尚未加载</h1>
              <p>请从当前可用导航中选择页面。</p>
            </section>
          ),
        },
      ],
    },
  ]);
}

export const router = createPlatformRouter();
