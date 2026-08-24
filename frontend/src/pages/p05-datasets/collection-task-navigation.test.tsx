// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../app/providers';
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from '../../shared/config/runtime';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  datasetIds,
  datasetListFixture,
  episodePageFixture,
} from '../../mocks/fixtures/datasets/core';
import { datasetHandlers } from '../../mocks/handlers/datasets.handlers';
import { DatasetDetailPage } from '../p06-dataset-detail/page';
import { DatasetsPage } from './page';

const server = setupServer(...datasetHandlers);
const collectionTaskId = 'assembly';

function createRouter(initialEntry: string) {
  return createMemoryRouter(
    [
      { path: '/datasets', element: <DatasetsPage /> },
      { path: '/datasets/:datasetId', element: <DatasetDetailPage /> },
      { path: '/collection-tasks', element: <h1>采集任务</h1> },
    ],
    { initialEntries: [initialEntry] },
  );
}

beforeAll(() => {
  configureRuntime({
    apiBaseUrl: 'http://localhost/api/v1',
    sseBaseUrl: 'http://localhost/api/v1/events',
    buildVersion: 'collection-task-navigation-test',
    releaseEnv: 'test',
  });
  server.listen({ onUnhandledRequest: 'error' });
});

beforeEach(() => {
  const scope = {
    organizationId: 'org_fx_01',
    projectId: 'prj_fx_01',
    regionCode: 'cn-shanghai',
  } as const;
  useShellStore.getState().setScope(scope);
  useShellStore.getState().setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: 'collection-task-navigation-v1',
    capabilities: ['dataset.read', 'episode.read'],
    fetchedAt: '2026-08-24T00:00:00Z',
  });
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  vi.unstubAllGlobals();
  useShellStore.getState().clearSensitiveState();
});

afterAll(() => {
  server.close();
  resetRuntimeConfigForTests();
});

describe('collection task dataset navigation', () => {
  it('shows every linked dataset and keeps the task filter on every Episode request', async () => {
    let listTask: string | null = null;
    const episodeTasks: string[] = [];
    let listRequests = 0;
    let episodeRequests = 0;
    const secondDatasetId = 'dataset_fx_02';
    const secondVersionId = 'version_fx_ready_02';
    const secondEpisodeId = 'episode_fx_02';
    server.use(
      http.get('*/projects/:projectId/datasets', ({ request }) => {
        listRequests += 1;
        listTask = new URL(request.url).searchParams.get('task');
        return HttpResponse.json({
          ...datasetListFixture,
          items: [
            datasetListFixture.items[0],
            {
              ...datasetListFixture.items[0],
              dataset_id: secondDatasetId,
              name: 'Second collection dataset',
              current_version: {
                ...datasetListFixture.items[0].current_version,
                version_id: secondVersionId,
                display_version: 'v2',
              },
            },
          ],
        });
      }),
      http.get(
        '*/projects/:projectId/datasets/:datasetId/versions/:versionId/episodes',
        ({ request, params }) => {
          episodeRequests += 1;
          episodeTasks.push(new URL(request.url).searchParams.get('task') ?? '');
          const second = String(params.datasetId) === secondDatasetId;
          const episodeId = second ? secondEpisodeId : datasetIds.episode;
          return HttpResponse.json({
            ...episodePageFixture,
            items: episodePageFixture.items.map((item) => ({
              ...item,
              dataset_id: String(params.datasetId),
              version_id: String(params.versionId),
              episode_id: episodeId,
              selected_revision: {
                ...item.selected_revision,
                episode_id: episodeId,
                revision_id: second ? 'revision_fx_02' : item.selected_revision.revision_id,
              },
            })),
          });
        },
      ),
    );
    const router = createRouter(`/datasets?collectionTaskId=${collectionTaskId}`);

    render(
      <ProviderHarness>
        <RouterProvider router={router} />
      </ProviderHarness>,
    );

    expect(await screen.findByText(datasetIds.episode)).toBeVisible();
    expect(await screen.findByText(secondEpisodeId)).toBeVisible();
    expect(screen.getByText('Assembly dataset')).toBeVisible();
    expect(screen.getByText('Second collection dataset')).toBeVisible();
    expect(screen.getByText('采集任务关联 2 个可浏览数据集')).toBeVisible();
    await waitFor(() => {
      expect(listTask).toBe(collectionTaskId);
      expect(listRequests).toBe(1);
      expect(episodeRequests).toBe(2);
      expect(episodeTasks).toEqual([collectionTaskId, collectionTaskId]);
    });
    expect(router.state.location.pathname).toBe('/datasets');
    expect(router.state.location.search).toBe(`?collectionTaskId=${collectionTaskId}`);
  });

  it('restores the same task-scoped aggregate view directly from the URL', async () => {
    const router = createRouter(`/datasets?collectionTaskId=${collectionTaskId}`);

    render(
      <ProviderHarness>
        <RouterProvider router={router} />
      </ProviderHarness>,
    );

    expect(await screen.findByText(datasetIds.episode)).toBeVisible();
    expect(screen.getByText('采集任务关联 1 个可浏览数据集')).toBeVisible();
    expect(router.state.location.pathname).toBe('/datasets');
    expect(router.state.location.search).toContain(`collectionTaskId=${collectionTaskId}`);
  });

  it('shows task-specific empty and invalid-parameter states without listing all datasets', async () => {
    const emptyRouter = createRouter('/datasets?collectionTaskId=task-without-data');
    const first = render(
      <ProviderHarness>
        <RouterProvider router={emptyRouter} />
      </ProviderHarness>,
    );
    expect(await screen.findByText('该采集任务暂无数据')).toBeVisible();
    expect(screen.getByRole('heading', { name: '采集任务数据' })).toBeVisible();
    expect(screen.queryByText(/正在解析采集任务/)).not.toBeInTheDocument();
    expect(screen.queryByText('已锁定采集任务范围')).not.toBeInTheDocument();
    expect(screen.queryByText('task-without-data')).not.toBeInTheDocument();
    expect(screen.queryByText(datasetListFixture.items[0].name)).not.toBeInTheDocument();
    first.unmount();

    const invalidRouter = createRouter('/datasets?collectionTaskId=%2Fbad');
    render(
      <ProviderHarness>
        <RouterProvider router={invalidRouter} />
      </ProviderHarness>,
    );
    expect(await screen.findByText('采集任务参数无效')).toBeVisible();
    expect(screen.queryByText(datasetListFixture.items[0].name)).not.toBeInTheDocument();
  });
});
