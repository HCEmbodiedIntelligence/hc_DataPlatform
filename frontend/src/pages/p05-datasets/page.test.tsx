// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../app/providers';
import { datasetListFixture } from '../../mocks/fixtures/datasets/core';
import { datasetHandlers } from '../../mocks/handlers/datasets.handlers';
import { configureRuntime, resetRuntimeConfigForTests } from '../../shared/config/runtime';
import { useShellStore } from '../../shared/scope/shell-store';
import { DatasetsPage } from './page';

const server = setupServer(...datasetHandlers);

function createRouter(initialEntry: string) {
  return createMemoryRouter([{ path: '/datasets', element: <DatasetsPage /> }], {
    initialEntries: [initialEntry],
  });
}

beforeAll(() => {
  configureRuntime({
    apiBaseUrl: 'http://localhost/api/v1',
    sseBaseUrl: 'http://localhost/api/v1/events',
    buildVersion: 'p05-page-test',
    releaseEnv: 'test',
  });
  server.listen({ onUnhandledRequest: 'error' });
});

beforeEach(() => {
  useShellStore.getState().setScope({
    organizationId: 'org_fx_01',
    projectId: 'prj_fx_01',
    regionCode: 'cn-shanghai',
  });
  useShellStore.getState().setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: 'p05-page-test-v1',
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
  vi.restoreAllMocks();
  useShellStore.getState().clearSensitiveState();
});

afterAll(() => {
  server.close();
  resetRuntimeConfigForTests();
});

describe('P05 datasets page filters', () => {
  it('shows the selected dataset summary when a table row is activated', async () => {
    const user = userEvent.setup();
    const router = createRouter('/datasets');

    render(
      <ProviderHarness>
        <RouterProvider router={router} />
      </ProviderHarness>,
    );

    expect(await screen.findByText('共 2 个数据集')).toBeVisible();
    const summary = screen.getByLabelText('当前选中数据集');
    expect(within(summary).getByRole('heading', { name: 'Assembly dataset' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '摘要' })).not.toBeInTheDocument();

    const waitingRow = screen.getByRole('row', {
      name: '选择 Waiting for ingest 并查看摘要',
    });
    expect(waitingRow).toHaveAttribute('aria-selected', 'false');
    await user.click(waitingRow);

    expect(within(summary).getByRole('heading', { name: 'Waiting for ingest' })).toBeVisible();
    expect(waitingRow).toHaveAttribute('aria-selected', 'true');

    const assemblyRow = screen.getByRole('row', {
      name: '选择 Assembly dataset 并查看摘要',
    });
    assemblyRow.focus();
    await user.keyboard('{Enter}');
    expect(within(summary).getByRole('heading', { name: 'Assembly dataset' })).toBeVisible();
  });

  it('keeps applied filters collapsed and shows datasetCount', async () => {
    const listQueries: URLSearchParams[] = [];
    server.use(
      http.get('*/projects/:projectId/datasets', ({ request }) => {
        listQueries.push(new URL(request.url).searchParams);
        return HttpResponse.json(datasetListFixture);
      }),
    );
    const router = createRouter(
      '/datasets?q=assembly&task=assembly&workflowState=pendingReview' +
        '&datasetCreatedFrom=2026-08-01&sort=nameAsc&limit=50',
    );

    render(
      <ProviderHarness>
        <RouterProvider router={router} />
      </ProviderHarness>,
    );

    expect(await screen.findByText('共 2 个数据集')).toBeVisible();
    expect(screen.getByRole('heading', { name: '筛选条件（4）' })).toBeVisible();
    expect(screen.getByRole('button', { name: '展开' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(screen.queryByText('待复核版本')).not.toBeInTheDocument();
    expect(screen.queryByText('已退回版本')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('页面摘要')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(router.state.location.search).toBe(
        '?q=assembly&task=assembly&workflowState=pendingReview' +
          '&datasetCreatedFrom=2026-08-01&sort=nameAsc&limit=50',
      );
      expect(listQueries.length).toBeGreaterThan(0);
      expect(listQueries.at(-1)?.get('q')).toBe('assembly');
      expect(listQueries.at(-1)?.get('task')).toBe('assembly');
      expect(listQueries.at(-1)?.get('workflow_state')).toBe('pendingReview');
    });
  });

  it('submits the single task input to the server and returns to the first page', async () => {
    const user = userEvent.setup();
    const tasks: Array<string | null> = [];
    server.use(
      http.get('*/projects/:projectId/datasets', ({ request }) => {
        tasks.push(new URL(request.url).searchParams.get('task'));
        return HttpResponse.json(datasetListFixture);
      }),
    );
    const router = createRouter('/datasets?after=cursor_fx_next');
    render(
      <ProviderHarness>
        <RouterProvider router={router} />
      </ProviderHarness>,
    );
    expect(await screen.findByText('共 2 个数据集')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '展开' }));

    const taskInput = screen.getByPlaceholderText('输入任务名称或 ID');
    expect(screen.getAllByPlaceholderText('输入任务名称或 ID')).toHaveLength(1);
    await user.type(taskInput, '  pick  ');
    await user.click(screen.getByRole('button', { name: '应用筛选' }));

    expect(screen.getByRole('button', { name: '展开' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    await waitFor(() => {
      expect(tasks.at(-1)).toBe('pick');
      expect(router.state.location.search).toBe('?task=pick');
    });
  });

  it('resets filters and cursors while preserving sort and page size', async () => {
    const user = userEvent.setup();
    const router = createRouter(
      '/datasets?q=assembly&task=pick&workflowState=returned&after=cursor_fx_next' +
        '&sort=nameAsc&limit=50',
    );
    render(
      <ProviderHarness>
        <RouterProvider router={router} />
      </ProviderHarness>,
    );
    expect(await screen.findByText('共 2 个数据集')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '展开' }));
    await user.click(screen.getByRole('button', { name: '重置' }));

    await waitFor(() => {
      expect(router.state.location.search).toBe('?sort=nameAsc&limit=50');
    });
    expect(screen.getByRole('button', { name: '收起' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });
});
