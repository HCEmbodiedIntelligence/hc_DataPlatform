import { Navigate, createBrowserRouter, type RouteObject } from 'react-router-dom';
import { PlatformShell } from '../shell/PlatformShell';
import { registerPageRoutes } from '../../shared/routing/route-registry';
import type { PageAvailability } from '../shell/navigation-manifest';
import { RouteCapabilityGuard } from './RouteCapabilityGuard';

type PageRouteModule = Record<string, unknown>;
type OwnedRouteObject = RouteObject & {
  readonly navigationOwnerGroupId: string;
  readonly navigationOwnerPageId: string;
  readonly requiredCapabilities: readonly string[];
  readonly hiddenFromNavigation?: boolean;
  readonly defaultGroupLanding?: boolean;
};

// P02–P04 route modules still expose static elements for their direct contract
// tests. Exclude those modules from the runtime aggregator so their page code
// is not pulled into the application entry chunk; the equivalent runtime
// records below retain ownership/capability metadata and load pages on demand.
const modules = import.meta.glob<PageRouteModule>(
  [
    '/src/pages/*/routes.tsx',
    '!/src/pages/p02-data-sources/routes.tsx',
    '!/src/pages/p03-upload-jobs/routes.tsx',
    '!/src/pages/p04-upload-detail/routes.tsx',
  ],
  { eager: true },
);

const lazyIngestPages: readonly {
  readonly pageId: 'P02' | 'P03' | 'P04';
  readonly routes: readonly OwnedRouteObject[];
}[] = [
  {
    pageId: 'P02',
    routes: [
      {
        path: '/ingest/sources',
        navigationOwnerGroupId: 'ingest',
        navigationOwnerPageId: 'P02',
        requiredCapabilities: ['ingest_source.read'],
        lazy: async () => ({
          Component: (await import('../../pages/p02-data-sources/page')).default,
        }),
      },
    ],
  },
  {
    pageId: 'P03',
    routes: [
      {
        path: '/ingest/uploads',
        navigationOwnerGroupId: 'ingest',
        navigationOwnerPageId: 'P03',
        requiredCapabilities: ['upload.read'],
        defaultGroupLanding: true,
        lazy: async () => ({
          Component: (await import('../../pages/p03-upload-jobs/page')).default,
        }),
      },
    ],
  },
  {
    pageId: 'P04',
    routes: [
      {
        path: '/ingest/uploads/:uploadId',
        navigationOwnerGroupId: 'ingest',
        navigationOwnerPageId: 'P03',
        requiredCapabilities: ['upload.read'],
        hiddenFromNavigation: true,
        lazy: async () => ({
          Component: (await import('../../pages/p04-upload-detail/page')).default,
        }),
      },
    ],
  },
];

function isRouteObject(value: unknown): value is RouteObject {
  return typeof value === 'object' && value !== null && ('path' in value || 'index' in value);
}

function isRouteArray(value: unknown): value is readonly RouteObject[] {
  return Array.isArray(value) && value.every(isRouteObject);
}

function pageIdFromModule(modulePath: string): string | null {
  const match = /\/pages\/p(\d{2})-[^/]+\/routes\.tsx$/u.exec(modulePath);
  return match?.[1] ? `P${match[1]}` : null;
}

const routeRecords: RouteObject[] = [];
const guardedRouteRecords: RouteObject[] = [];
const availability: Record<string, boolean> = {};

const pageReadCapability: Readonly<Record<string, string | null>> = {
  P01: null,
  P02: 'ingest_source.read',
  P03: 'upload.read',
  P04: 'upload.read',
  P05: 'dataset.read',
  P06: 'dataset.read',
  P07: 'dataset_version.read',
  P08: 'annotation_task.read',
  P09: 'manual_issue.read',
  P10: 'cleaning.read',
  P11: 'cleaning.read',
  P12: 'storage.overview.read',
  P13: 'storage.lifecycle.read',
  P14: 'robot_model.read',
  P15: 'robot.read',
  P16: 'calibration.read',
  P17: 'data_schema.read',
  P18: 'access.read',
  P19: 'audit.read',
};

function routeCapabilities(pageId: string, route: RouteObject): readonly string[] {
  const record = route as RouteObject & { requiredCapabilities?: unknown };
  if (
    Array.isArray(record.requiredCapabilities) &&
    record.requiredCapabilities.every((value) => typeof value === 'string')
  ) {
    return record.requiredCapabilities;
  }
  if (typeof route.path === 'string' && route.path.includes('/episodes/:episodeId/view')) {
    return ['episode.read'];
  }
  const capability = pageReadCapability[pageId];
  return capability ? [capability] : [];
}

for (const [modulePath, routeModule] of Object.entries(modules)) {
  const pageId = pageIdFromModule(modulePath);
  if (pageId === null) continue;
  const seen = new Set<readonly RouteObject[]>();
  for (const exported of Object.values(routeModule)) {
    if (!isRouteArray(exported) || seen.has(exported)) continue;
    seen.add(exported);
    const heavy = ['P08', 'P11', 'P14', 'P16'].includes(pageId);
    const accepted = exported.filter((route) => {
      if (heavy && route.lazy === undefined) {
        console.error('heavy_route_not_lazy', { pageId, path: route.path ?? '<index>' });
        return false;
      }
      return true;
    });
    if (accepted.length === 0) continue;
    routeRecords.push(...accepted);
    guardedRouteRecords.push(
      ...accepted.map((route) => ({
        element: <RouteCapabilityGuard requiredCapabilities={routeCapabilities(pageId, route)} />,
        children: [route],
      })),
    );
    registerPageRoutes(
      pageId,
      accepted.flatMap((route) => (route.path ? [route.path] : [])),
    );
    availability[pageId] = true;
  }
}

for (const page of lazyIngestPages) {
  routeRecords.push(...page.routes);
  guardedRouteRecords.push(
    ...page.routes.map((route) => ({
      element: (
        <RouteCapabilityGuard requiredCapabilities={routeCapabilities(page.pageId, route)} />
      ),
      children: [route],
    })),
  );
  registerPageRoutes(
    page.pageId,
    page.routes.flatMap((route) => (route.path ? [route.path] : [])),
  );
  availability[page.pageId] = true;
}

export const aggregatedPageRoutes: readonly RouteObject[] = routeRecords;
export const pageAvailability: PageAvailability = Object.freeze(availability);

const firstRoute =
  routeRecords.find((route) => typeof route.path === 'string')?.path ?? '/dashboard';

export function createPlatformRouter() {
  return createBrowserRouter([
    {
      path: '/',
      element: <PlatformShell pageAvailability={pageAvailability} />,
      children: [
        { index: true, element: <Navigate replace to={firstRoute} /> },
        ...guardedRouteRecords,
        {
          path: '*',
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
