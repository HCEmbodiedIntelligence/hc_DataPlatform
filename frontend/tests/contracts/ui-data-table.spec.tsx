import type { ColumnDef, ColumnFiltersState, SortingState } from '@tanstack/react-table';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../src/app/providers';
import { CursorPager, DataTable } from '../../src/shared/ui/data';
import { validateDataTableRows } from '../../src/shared/ui/data/row-contract';

interface FixtureRow {
  id: string;
  name: string;
  state: string;
}

const rows: readonly FixtureRow[] = [
  { id: 'row_alpha', name: 'Alpha', state: 'READY' },
  { id: 'row_beta', name: 'Beta', state: 'PAUSED' },
];

const columns: readonly ColumnDef<FixtureRow, unknown>[] = [
  {
    accessorKey: 'name',
    header: '名称',
    cell: ({ getValue }) => String(getValue()),
    filterFn: 'includesString',
  },
  {
    accessorKey: 'state',
    header: '状态',
    cell: ({ getValue }) => String(getValue()),
  },
];

class ResizeObserverMock implements ResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element));
  vi.stubGlobal('ResizeObserver', ResizeObserverMock);
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
});

function InteractiveTable() {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([
    { id: 'name', value: 'Beta' },
  ]);
  const [selectedRowIds, setSelectedRowIds] = useState<readonly string[]>([]);
  return (
    <ProviderHarness>
      <DataTable
        data={rows}
        columns={columns}
        getRowId={(row) => row.id}
        caption="受控数据表"
        sorting={sorting}
        onSortingChange={setSorting}
        columnFilters={columnFilters}
        onColumnFiltersChange={setColumnFilters}
        selection={{
          selectedRowIds,
          onSelectedRowIdsChange: setSelectedRowIds,
          getRowSelectionLabel: (row) => `选择 ${row.name}`,
        }}
      />
      <output data-testid="sorting">
        {sorting.map((item) => `${item.id}:${item.desc ? 'desc' : 'asc'}`).join(',')}
      </output>
      <output data-testid="selection">{selectedRowIds.join(',')}</output>
    </ProviderHarness>
  );
}

describe('DataTable and CursorPager contracts', () => {
  it('keeps sorting, filtering and selection controlled while Ant Table only renders', async () => {
    const user = userEvent.setup();
    const view = render(<InteractiveTable />);

    expect(screen.getByRole('region', { name: '受控数据表' })).toHaveAttribute(
      'data-pagination-contract',
      'cursor-only',
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(view.container.querySelector('.ant-pagination')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'name：未排序，激活以升序排列' }));
    expect(screen.getByTestId('sorting')).toHaveTextContent('name:asc');
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('Alpha');
    expect(screen.getAllByRole('row')[2]).toHaveTextContent('Beta');

    await user.click(screen.getByRole('checkbox', { name: '选择 Alpha' }));
    expect(screen.getByTestId('selection')).toHaveTextContent('row_alpha');
  });

  it('renders loading, empty and error as explicit accessible states', () => {
    const view = render(
      <ProviderHarness>
        <DataTable
          data={[]}
          columns={columns}
          getRowId={(row) => row.id}
          caption="状态表"
          state="loading"
        />
      </ProviderHarness>,
    );
    expect(screen.getByRole('region', { name: '状态表' })).toHaveAttribute('aria-busy', 'true');

    view.rerender(
      <ProviderHarness>
        <DataTable
          data={[]}
          columns={columns}
          getRowId={(row) => row.id}
          caption="状态表"
          state="empty"
          empty="没有匹配项"
        />
      </ProviderHarness>,
    );
    expect(screen.getByText('没有匹配项')).toBeInTheDocument();

    view.rerender(
      <ProviderHarness>
        <DataTable
          data={rows}
          columns={columns}
          getRowId={(row) => row.id}
          caption="状态表"
          state="error"
          error={<span role="alert">合同不匹配，已停止展示</span>}
        />
      </ProviderHarness>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('合同不匹配，已停止展示');
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
  });

  it('fails closed for unstable IDs and never creates client-side business pages', () => {
    expect(() => validateDataTableRows([rows[0]!, rows[0]!], (row) => row.id)).toThrow(
      /duplicate stable row ID/,
    );
    expect(() => validateDataTableRows(rows, () => ' ')).toThrow(/non-empty stable row ID/);

    const tenThousandRows = Array.from({ length: 10_000 }, (_, index) => ({
      id: `row_${index}`,
      name: `Row ${index}`,
      state: 'READY',
    }));
    const validated = validateDataTableRows(tenThousandRows, (row) => row.id);
    expect(validated).toHaveLength(10_000);
    expect(validated[0]?.id).toBe('row_0');
    expect(validated.at(-1)?.id).toBe('row_9999');
  });

  it('emits mutually exclusive before and after cursors with keyboard-accessible controls', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const view = render(
      <ProviderHarness>
        <CursorPager
          pageInfo={{
            startCursor: 'cursor_start',
            endCursor: 'cursor_end',
            hasPreviousPage: true,
            hasNextPage: true,
          }}
          onChange={onChange}
        />
      </ProviderHarness>,
    );

    expect(screen.getByRole('navigation', { name: '游标分页' })).toHaveAttribute(
      'data-pagination-contract',
      'after-before',
    );
    expect(view.container.querySelector('.ant-pagination')).not.toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole('button', { name: '上一组' })).toHaveFocus();
    await user.keyboard('{Enter}');
    await user.click(screen.getByRole('button', { name: '下一组' }));
    expect(onChange).toHaveBeenNthCalledWith(1, { before: 'cursor_start' });
    expect(onChange).toHaveBeenNthCalledWith(2, { after: 'cursor_end' });

    view.rerender(
      <ProviderHarness>
        <CursorPager
          pageInfo={{
            startCursor: null,
            endCursor: null,
            hasPreviousPage: true,
            hasNextPage: true,
          }}
          onChange={onChange}
        />
      </ProviderHarness>,
    );
    expect(screen.getByRole('button', { name: '上一组' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下一组' })).toBeDisabled();
  });
});
