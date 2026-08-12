import { readFileSync } from 'node:fs';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../src/app/providers';
import { adaptDatasetListEnvelope } from '../../src/features/datasets/api/adapters';
import { datasetListEnvelopeWireSchema } from '../../src/features/datasets/api/wire-schemas';
import { routes } from '../../src/features/datasets/routing';
import { setScenario } from '../../src/mocks';
import { datasetListFixture } from '../../src/mocks/fixtures/datasets/core';
import DatasetsPage from '../../src/pages/p05-datasets/page';
import datasetsQueryCodec from '../../src/pages/p05-datasets/query-codec';
import p05Routes from '../../src/pages/p05-datasets/routes';
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

function renderPage(path = '/datasets') {
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
          <DatasetsPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ProviderHarness>,
  );
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: '/api/v1',
    sseBaseUrl: '/api/v1/events',
    buildVersion: 'p05-contract',
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

describe('P05 invariant contracts', () => {
  it('keeps route, query, schema and adapter identities unchanged', () => {
    expect(p05Routes).toHaveLength(1);
    expect(p05Routes[0]?.path).toBe('/datasets');
    expect(p05Routes[0]?.lazy).toBeTypeOf('function');
    expect(routes.datasets.build({ q: 'assembly', limit: 50 })).toBe('/datasets?q=assembly&limit=50');

    const parsed = datasetsQueryCodec.parse(
      'q=assembly&channels=%2Fjoint&channels=%2Fcamera%2Ffront&channelMatch=any&sort=nameAsc&limit=50&after=cursor',
    );
    expect(parsed).toMatchObject({
      q: 'assembly',
      channels: ['/camera/front', '/joint'],
      channelMatch: 'any',
      sort: 'nameAsc',
      limit: 50,
      after: 'cursor',
    });
    const changed = datasetsQueryCodec.withChanges(parsed, { task: 'assembly' });
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();

    const page = adaptDatasetListEnvelope(datasetListEnvelopeWireSchema.parse(datasetListFixture));
    expect(page.items[0]).toMatchObject({ datasetId: 'dataset_fx_01', name: 'Assembly dataset' });
    expect(page.pageInfo).toMatchObject({ hasNextPage: false, hasPreviousPage: false });
  });

  it('keeps API paths, scoped query keys and route capability outside presentation', () => {
    const queries = readFileSync('src/features/datasets/api/queries.ts', 'utf8');
    const hooks = readFileSync('src/features/datasets/api/hooks.ts', 'utf8');
    const router = readFileSync('src/app/router/index.tsx', 'utf8');
    const page = readFileSync('src/pages/p05-datasets/page.tsx', 'utf8');

    expect(queries).toContain("projectPath('/datasets')");
    expect(queries).toContain("projectPath('/datasets:summary')");
    expect(queries).toContain("projectPath('/datasets:facets')");
    expect(queries).toContain("projectPath('/datasets:page-capabilities')");
    expect(hooks).toContain("useScopedKey('datasets', 'list', normalized)");
    expect(hooks).toContain("useScopedKey('datasets', 'summary', normalized)");
    expect(hooks).toContain("useScopedKey('datasets', 'facets', normalized)");
    expect(router).toContain("P05: 'dataset.read'");
    expect(page).not.toMatch(
      /\bfetch\s*\(|['"](?:https?:\/\/[^'"]+)?\/api(?:\/|['"])|PROJECT_(?:ADMIN|DEVELOPER|DATA_PROCESSOR)/u,
    );
  });
});

describe('P05 shared-UI migration contract', () => {
  it('uses public shared UI, CSS Modules and no native dialog or local table', () => {
    const page = readFileSync('src/pages/p05-datasets/page.tsx', 'utf8');
    const table = readFileSync('src/pages/p05-datasets/components/DatasetTable.tsx', 'utf8');
    const filters = readFileSync('src/pages/p05-datasets/components/DatasetFilterPanel.tsx', 'utf8');
    const editor = readFileSync('src/pages/p05-datasets/components/CreateDatasetDialog.tsx', 'utf8');

    expect(page).toContain("from '../../shared/ui'");
    expect(page).toContain("from './styles.module.css'");
    expect(page).not.toContain('../../features/datasets/components/datasets.css');
    expect(table).toContain('DataTable');
    expect(table).not.toMatch(/<table\b|<thead\b|<tbody\b/u);
    expect(filters).toContain('FilterToolbar');
    expect(editor).toContain('createZodResolver');
    expect(editor).not.toMatch(/<dialog(?:\s|>)/u);
  });

  it('renders shared summary, high-density table, create modal and partial-region recovery', async () => {
    const user = userEvent.setup();
    await act(() => setScenario('datasets', 'happy'));
    const happy = renderPage();

    expect(await screen.findByRole('heading', { level: 1, name: '数据集' })).toBeInTheDocument();
    expect(await screen.findByText('dataset_fx_01')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Episodes' })).toHaveTextContent('1');
    const create = screen.getByRole('button', { name: '创建数据集' });
    await waitFor(() => expect(create).toBeEnabled());
    await user.click(create);
    const modalTitle = (await screen.findAllByText('创建数据集')).find((element) =>
      element.classList.contains('ant-modal-title'),
    );
    expect(modalTitle).toBeDefined();
    const dialog = modalTitle?.closest<HTMLElement>('[role="dialog"]') ?? null;
    expect(dialog).not.toBeNull();
    if (!dialog) throw new Error('Create dataset dialog is missing its dialog boundary');
    expect(dialog).toHaveTextContent('不隐式创建 Version');
    const name = within(dialog).getByLabelText('名称');
    const submit = within(dialog).getByRole('button', { name: /创\s*建/u });
    expect(submit).toBeDisabled();
    await user.type(name, '新数据集');
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(within(dialog).getByRole('button', { name: /取\s*消/u }));
    happy.unmount();

    await act(() => setScenario('datasets', 'partial-error'));
    renderPage();
    expect(await screen.findByText('摘要区域加载失败')).toBeInTheDocument();
    expect(screen.getByText('筛选项加载失败')).toBeInTheDocument();
    expect(await screen.findByText('dataset_fx_01')).toBeInTheDocument();
  });

  it('fails closed for forbidden, unknown enum and contract mismatch without leaking payloads', async () => {
    await act(() => setScenario('datasets', 'forbidden'));
    const forbidden = renderPage();
    expect(await screen.findByText('无权访问')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建数据集' })).toBeDisabled();
    forbidden.unmount();

    await act(() => setScenario('datasets', 'unknown-enum'));
    const unknown = renderPage();
    expect(await screen.findByLabelText('状态：未知状态')).toBeInTheDocument();
    unknown.unmount();

    await act(() => setScenario('datasets', 'contract-mismatch'));
    renderPage();
    expect(await screen.findByText('数据合同不匹配')).toBeInTheDocument();
    expect(screen.queryByText('fixture.invalid/leak')).not.toBeInTheDocument();
  });
});
