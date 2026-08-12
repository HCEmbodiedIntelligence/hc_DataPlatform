import { readFileSync } from 'node:fs';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../src/app/providers';
import { adaptStorageOverview } from '../../src/features/storage-overview/api/adapter';
import { storageOverviewWireSchema } from '../../src/features/storage-overview/api/schemas';
import {
  patchStorageOverviewSearch,
  storageOverviewRoute,
} from '../../src/features/storage-overview/routing';
import { setScenario } from '../../src/mocks';
import { storageOverviewFixture } from '../../src/mocks/fixtures/storage-overview';
import StorageOverviewPage from '../../src/pages/p12-storage-overview/page';
import storageOverviewQueryCodec from '../../src/pages/p12-storage-overview/query-codec';
import p12Routes from '../../src/pages/p12-storage-overview/routes';
import { configureRuntime } from '../../src/shared/config/runtime';
import { useShellStore } from '../../src/shared/scope/shell-store';

vi.mock('../../src/features/storage-overview/storage-charts', () => ({
  default: () => <div role="img" aria-label="存储图表合同替身" />,
}));

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

function renderPage(path = '/storage/overview') {
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
          <StorageOverviewPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ProviderHarness>,
  );
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: '/api/v1',
    sseBaseUrl: '/api/v1/events',
    buildVersion: 'p12-contract',
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

describe('P12 invariant contracts', () => {
  it('keeps route, query, schema and exact-string adapter identities unchanged', () => {
    expect(p12Routes).toHaveLength(1);
    expect(p12Routes[0]?.path).toBe('/storage/overview');
    expect(p12Routes[0]?.lazy).toBeTypeOf('function');
    expect(storageOverviewRoute.build({ tab: 'objects', limit: 100 })).toBe(
      '/storage/overview?tab=objects&limit=100',
    );

    const parsed = storageOverviewQueryCodec.parse(
      'tab=objects&objectRole=SOURCE&storageClass=STANDARD&anomaly=LARGE_OBJECT&status=AVAILABLE&months=12&limit=100&after=cursor',
    );
    expect(parsed).toMatchObject({
      tab: 'objects',
      objectRole: 'SOURCE',
      storageClass: 'STANDARD',
      anomaly: ['LARGE_OBJECT'],
      status: ['AVAILABLE'],
      months: 12,
      limit: 100,
      after: 'cursor',
    });
    expect(patchStorageOverviewSearch(parsed, { limit: 50 }).after).toBeUndefined();

    const overview = adaptStorageOverview(storageOverviewWireSchema.parse(storageOverviewFixture));
    expect(overview.snapshotId).toBe('snapshot_fx_storage_01');
    expect(overview.totals.actualOssPhysicalBytes).toEqual({
      state: 'KNOWN',
      value: '2147483648',
    });
  });

  it('keeps five read-only API paths, scoped query keys and route capability outside presentation', () => {
    const client = readFileSync('src/features/storage-overview/api/client.ts', 'utf8');
    const queryKeys = readFileSync('src/features/storage-overview/api/query-keys.ts', 'utf8');
    const router = readFileSync('src/app/router/index.tsx', 'utf8');
    const page = readFileSync('src/pages/p12-storage-overview/page.tsx', 'utf8');

    expect(client).toContain("`${storageRoot(scope)}/overview`");
    expect(client).toContain("`${storageRoot(scope)}/objects`");
    expect(client).toContain("`${storageRoot(scope)}/objects/${encodeURIComponent(objectId)}`");
    expect(client).toContain("`${storageRoot(scope)}/multipart`");
    expect(client).toContain("`${storageRoot(scope)}/cost-breakdown`");
    expect(queryKeys).toContain("makeQueryKey('storage', 'overview'");
    expect(queryKeys).toContain("makeQueryKey('storage', 'objects'");
    expect(queryKeys).toContain("makeQueryKey('storage', 'object'");
    expect(queryKeys).toContain("makeQueryKey('storage', 'multipart'");
    expect(queryKeys).toContain("makeQueryKey('storage', 'cost'");
    expect(router).toContain("P12: 'storage.overview.read'");
    expect(page).not.toMatch(
      /\bfetch\s*\(|['"](?:https?:\/\/[^'"]+)?\/api(?:\/|['"])|PROJECT_(?:ADMIN|DEVELOPER|DATA_PROCESSOR)|useMutation/u,
    );
  });
});

describe('P12 shared-UI migration contract', () => {
  it('uses public shared UI and CSS Modules without native tables or a local drawer', () => {
    const page = readFileSync('src/pages/p12-storage-overview/page.tsx', 'utf8');
    const inventory = readFileSync(
      'src/pages/p12-storage-overview/components/StorageInventoryTable.tsx',
      'utf8',
    );
    const multipart = readFileSync(
      'src/pages/p12-storage-overview/components/StorageMultipartTable.tsx',
      'utf8',
    );
    const drawer = readFileSync(
      'src/pages/p12-storage-overview/components/StorageObjectDrawer.tsx',
      'utf8',
    );
    const summary = readFileSync(
      'src/pages/p12-storage-overview/components/StorageSummaryStrip.tsx',
      'utf8',
    );

    expect(page).toContain("from '../../shared/ui'");
    expect(page).toContain("from './styles.module.css'");
    expect(page).not.toContain('StorageRegionState');
    expect(page).not.toContain('style={{');
    expect(inventory).toContain('DataTable');
    expect(multipart).toContain('DataTable');
    expect(drawer).toContain('EntityDrawer');
    expect(summary).toContain('UiMetricCard');
    expect(`${page}${inventory}${multipart}${drawer}`).not.toMatch(
      /<(?:table|thead|tbody|aside|dialog)(?:\s|>)/u,
    );
  });

  it('renders metrics, semantic tabs, both tables, the read-only drawer and cost facts', async () => {
    const user = userEvent.setup();
    await act(() => setScenario('storage-overview', 'happy'));
    renderPage();

    expect(await screen.findByRole('heading', { level: 1, name: '存储容量' })).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: '实际 OSS 容量' })).toHaveTextContent('2 GiB');
    expect(screen.getByRole('tab', { name: '概览' })).toHaveAttribute('aria-selected', 'true');

    await user.click(screen.getByRole('tab', { name: 'Inventory 对象' }));
    const inventory = await screen.findByRole('region', { name: '同一快照下的存储对象事实' });
    await user.click(within(inventory).getByRole('button', { name: /source\/•••\/02/u }));
    const dialogTitle = (await screen.findAllByText('对象详情')).find((element) =>
      element.classList.contains('ant-drawer-title'),
    );
    const dialog = dialogTitle?.closest<HTMLElement>('[role="dialog"]') ?? null;
    expect(dialog).not.toBeNull();
    if (!dialog) throw new Error('Storage object drawer is missing its dialog boundary');
    expect(dialog).toHaveTextContent('只读诊断');
    await user.click(within(dialog).getByRole('button', { name: '关闭对象详情' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '对象详情' })).not.toBeInTheDocument());

    await user.click(screen.getByRole('tab', { name: 'Multipart 诊断' }));
    expect(await screen.findByRole('region', { name: '只读 Multipart 上传诊断' })).toHaveTextContent(
      'multipart_fx_01',
    );
    await user.click(screen.getByRole('tab', { name: '费用' }));
    expect(await screen.findByRole('heading', { level: 2, name: '2026-08 费用构成' })).toBeInTheDocument();
    expect(screen.getAllByText('CNY 1288.00')).toHaveLength(2);
  });

  it('keeps partial failures regional and fails closed for forbidden, unknown and invalid wire data', async () => {
    await act(() => setScenario('storage-overview', 'partial-error'));
    const partial = renderPage('/storage/overview?tab=objects');
    expect(
      await screen.findByText('此区域暂时不可用', {}, { timeout: 8_000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '实际 OSS 容量' })).toHaveTextContent('2 GiB');
    partial.unmount();

    await act(() => setScenario('storage-overview', 'forbidden'));
    const forbidden = renderPage();
    expect(await screen.findByText('无权访问')).toBeInTheDocument();
    forbidden.unmount();

    await act(() => setScenario('storage-overview', 'unknown-enum'));
    const unknown = renderPage();
    expect(await screen.findByText('发现未知状态')).toBeInTheDocument();
    unknown.unmount();

    await act(() => setScenario('storage-overview', 'contract-mismatch'));
    renderPage();
    expect(
      await screen.findByText('存储响应不符合合同', {}, { timeout: 8_000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText('2147483648 B')).not.toBeInTheDocument();
  }, 20_000);
});
