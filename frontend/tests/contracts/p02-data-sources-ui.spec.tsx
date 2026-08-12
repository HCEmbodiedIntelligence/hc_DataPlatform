import { readFileSync } from 'node:fs';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../src/app/providers';
import { adaptDataSource, adaptDataSourcePage } from '../../src/features/ingest/api/adapters';
import { dataSourcePageWireSchema, dataSourceWireSchema } from '../../src/features/ingest/api/wire-schemas';
import { routes } from '../../src/features/ingest/routing';
import { dataSourceFixture, dataSourcePageFixture } from '../../src/mocks/fixtures/ingest';
import { setScenario } from '../../src/mocks';
import DataSourcesPage from '../../src/pages/p02-data-sources/page';
import { dataSourcesQueryCodec, updateDataSourcesSearch } from '../../src/pages/p02-data-sources/query-codec';
import dataSourceRouteRecords from '../../src/pages/p02-data-sources/routes';
import { configureRuntime } from '../../src/shared/config/runtime';
import { useShellStore } from '../../src/shared/scope/shell-store';

const originalMatchMedia = window.matchMedia;

class ResizeObserverMock implements ResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}

function resetShell(): void {
  useShellStore.setState({
    principal: null,
    sessionToken: null,
    scope: null,
    scopeKey: 'unscoped/-/-' as never,
    scopeChanging: false,
    authorization: null,
    authorizationLoading: false,
    authorizationFailed: false,
  });
}

function renderPage(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <ProviderHarness>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <DataSourcesPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ProviderHarness>,
  );
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: '/api/v1',
    sseBaseUrl: '/api/v1/events',
    buildVersion: 'p02-contract',
    releaseEnv: 'test',
  });
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }),
  });
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element));
  vi.stubGlobal('ResizeObserver', ResizeObserverMock);
});

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: originalMatchMedia });
  vi.unstubAllGlobals();
  resetShell();
});

describe('P02 invariant contracts', () => {
  it('keeps the route, query codec, wire schema and adapter identities unchanged', () => {
    expect(dataSourceRouteRecords).toEqual([
      expect.objectContaining({
        path: '/ingest/sources',
        navigationOwnerGroupId: 'ingest',
        navigationOwnerPageId: 'P02',
        requiredCapabilities: ['ingest_source.read'],
      }),
    ]);
    expect(routes.sources.build()).toBe('/ingest/sources');

    const parsed = dataSourcesQueryCodec.parse(
      'sourceType=ROBOT&sourceType=OSS_IMPORT&connectivity=ONLINE&after=cursor&limit=50',
    );
    expect(parsed).toMatchObject({
      sourceType: ['OSS_IMPORT', 'ROBOT'],
      connectivity: ['ONLINE'],
      after: 'cursor',
      limit: 50,
    });
    const changed = updateDataSourcesSearch(parsed, { q: 'station' });
    expect(changed.q).toBe('station');
    expect(changed).not.toHaveProperty('after');
    expect(changed).not.toHaveProperty('before');

    const page = adaptDataSourcePage(dataSourcePageWireSchema.parse(dataSourcePageFixture));
    const detail = adaptDataSource(dataSourceWireSchema.parse(dataSourceFixture));
    expect(page.items[0]).toMatchObject({ id: 'source_fx_01', configVersion: '7' });
    expect(page.pageInfo).toEqual({
      hasNextPage: false,
      hasPreviousPage: false,
      startCursor: null,
      endCursor: null,
    });
    expect(detail).toMatchObject({ id: 'source_fx_01', etag: 'source-rv-9' });
  });

  it('keeps API paths and scoped query-key resources outside the presentation layer', () => {
    const client = readFileSync('src/features/ingest/api/client.ts', 'utf8');
    const queries = readFileSync('src/features/ingest/api/queries.ts', 'utf8');
    const page = readFileSync('src/pages/p02-data-sources/page.tsx', 'utf8');

    expect(client).toContain("const path = `${resourceRoot(scope)}/data-sources/page`");
    expect(client).toContain("const path = `${resourceRoot(scope)}/data-sources/${encodeURIComponent(sourceId)}`");
    expect(queries).toContain("makeQueryKey('ingest', 'data-source-page', filters)");
    expect(queries).toContain("makeQueryKey('ingest', 'data-source', sourceId)");
    expect(page).not.toMatch(
      /\bfetch\s*\(|['"](?:https?:\/\/[^'"]+)?\/api(?:\/|['"])|PROJECT_(?:ADMIN|DEVELOPER|DATA_PROCESSOR)/u,
    );
  });
});

describe('P02 shared-UI migration contract', () => {
  it('uses the single shared UI boundary, CSS Modules and no native dialog or local table API', () => {
    const page = readFileSync('src/pages/p02-data-sources/page.tsx', 'utf8');
    const table = readFileSync('src/pages/p02-data-sources/components/DataSourceTable.tsx', 'utf8');
    const editor = readFileSync('src/pages/p02-data-sources/components/SourceEditorDialog.tsx', 'utf8');
    const actions = readFileSync('src/pages/p02-data-sources/components/SourceActionDialogs.tsx', 'utf8');

    expect(page).toContain("from '../../shared/ui'");
    expect(page).toContain("from './styles.module.css'");
    expect(table).toContain('DataTable');
    expect(table).not.toMatch(/<table\b|<thead\b|<tbody\b/u);
    expect(`${editor}\n${actions}`).not.toMatch(/<dialog(?:\s|>)/u);
    expect(page).not.toContain("../../features/ingest/styles.css");
  });

  it('renders the happy path through shared table, status, drawer and capability gates', async () => {
    const user = userEvent.setup();
    await act(() => setScenario('ingest', 'happy'));
    renderPage('/ingest/sources');

    expect(await screen.findByRole('heading', { name: '数据源' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /查看 上海采集站 A/u })).toBeEnabled();
    expect(screen.getByRole('button', { name: '新建数据源' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '数据源总数' })).toHaveTextContent('1');

    await user.click(screen.getByRole('button', { name: /查看 上海采集站 A/u }));
    const inspector = await screen.findByRole('complementary', { name: '数据源详情' });
    expect(inspector).toHaveTextContent('source_fx_01');
    expect(within(inspector).getByRole('button', { name: /编\s*辑/u })).toBeEnabled();
    expect(within(inspector).getByRole('button', { name: '测试连接' })).toBeEnabled();

    const stateButton = within(inspector).getByRole('button', { name: /停\s*用/u });
    expect(stateButton).toBeEnabled();
    await user.click(stateButton);
    const danger = (await screen.findByText('确认停用数据源')).closest<HTMLElement>('[role="dialog"]');
    expect(danger).not.toBeNull();
    if (!danger) throw new Error('Danger confirmation dialog is missing its dialog boundary');
    expect(danger).toHaveTextContent('source_fx_01');
    expect(danger).toHaveTextContent('source-rv-9');
    const confirm = within(danger).getByRole('button', { name: '确认停用' });
    expect(confirm).toBeDisabled();
    expect(within(danger).getByLabelText(/请输入资源 ID source_fx_01 以确认/u)).toBeInTheDocument();
    await user.click(within(danger).getByRole('button', { name: /取\s*消/u }));
    await waitFor(() => expect(screen.queryByText('确认停用数据源')).not.toBeInTheDocument());
  });

  it('fails closed for forbidden, unknown and contract-mismatched facts without leaking payloads', async () => {
    await act(() => setScenario('ingest', 'forbidden'));
    const forbidden = renderPage('/ingest/sources');
    expect(await screen.findByText('无权访问')).toBeInTheDocument();
    expect(screen.queryByText('新建数据源')).not.toBeInTheDocument();
    forbidden.unmount();

    await act(() => setScenario('ingest', 'unknown-enum'));
    const unknown = renderPage('/ingest/sources');
    await userEvent.click(await screen.findByRole('button', { name: /查看 上海采集站 A/u }));
    const inspector = await screen.findByRole('complementary', { name: '数据源详情' });
    expect(await within(inspector).findByText(/未知连接器类型/u)).toBeInTheDocument();
    expect(within(inspector).getByRole('button', { name: /编\s*辑/u })).toBeDisabled();
    expect(within(inspector).getByRole('button', { name: '轮换凭据' })).toBeDisabled();
    expect(within(inspector).getByRole('button', { name: '测试连接' })).toBeDisabled();
    unknown.unmount();

    await act(() => setScenario('ingest', 'contract-mismatch'));
    renderPage('/ingest/sources');
    expect(await screen.findByText('数据合同不匹配')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('fixture-only-leak')).not.toBeInTheDocument());
  });
});
