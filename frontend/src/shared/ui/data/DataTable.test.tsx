// @vitest-environment jsdom

import type { ColumnDef } from '@tanstack/react-table';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DataTable } from './DataTable';

type Row = Readonly<{
  id: string;
  name: string;
  status: string;
}>;

const columns: readonly ColumnDef<Row, unknown>[] = [
  {
    id: 'name',
    header: '名称',
    size: 160,
    cell: ({ row }) => row.original.name,
  },
  {
    id: 'status',
    header: '状态',
    size: 100,
    meta: { responsive: ['xl'] },
    cell: ({ row }) => row.original.status,
  },
];

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) =>
    getComputedStyle(element),
  );
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
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('DataTable stable column layout', () => {
  it('renders every column on the first pass with a fixed computed width', () => {
    const { container, rerender } = render(
      <DataTable
        data={[]}
        columns={columns}
        getRowId={(row) => row.id}
        caption="稳定表格"
        columnLayout="stable"
      />,
    );

    expect(screen.getByRole('columnheader', { name: '名称' })).toBeVisible();
    expect(screen.getByRole('columnheader', { name: '状态' })).toBeVisible();

    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    expect(table).toHaveStyle({
      width: '260px',
      minWidth: '100%',
      tableLayout: 'fixed',
    });

    expect(
      [...container.querySelectorAll('col')].map((column) => column.style.width),
    ).toEqual(['160px', '100px']);

    rerender(
      <DataTable
        data={[{ id: 'source-1', name: '机器人 A', status: '在线' }]}
        columns={columns}
        getRowId={(row) => row.id}
        caption="稳定表格"
        columnLayout="stable"
      />,
    );

    expect(screen.getByRole('cell', { name: '机器人 A' })).toBeVisible();
    expect(container.querySelector('table')).toHaveStyle({
      width: '260px',
      minWidth: '100%',
      tableLayout: 'fixed',
    });
    expect(
      [...container.querySelectorAll('col')].map((column) => column.style.width),
    ).toEqual(['160px', '100px']);
  });

  it('activates an interactive row by pointer or keyboard without swallowing nested controls', async () => {
    const user = userEvent.setup();
    const onActivate = vi.fn();
    const onOpen = vi.fn();
    const interactiveColumns: readonly ColumnDef<Row, unknown>[] = [
      ...columns,
      {
        id: 'actions',
        header: '操作',
        cell: ({ row }) => <button onClick={() => onOpen(row.original)}>打开</button>,
      },
    ];
    const item = { id: 'source-1', name: '机器人 A', status: '在线' };

    render(
      <DataTable
        data={[item]}
        columns={interactiveColumns}
        getRowId={(row) => row.id}
        rowInteraction={{
          activeRowId: null,
          onActivate,
          getActivationLabel: (row) => `查看 ${row.name}`,
        }}
      />,
    );

    const row = screen.getByRole('row', { name: '查看 机器人 A' });
    await user.click(row);
    expect(onActivate).toHaveBeenCalledTimes(1);

    row.focus();
    await user.keyboard(' ');
    expect(onActivate).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole('button', { name: '打开' }));
    expect(onOpen).toHaveBeenCalledWith(item);
    expect(onActivate).toHaveBeenCalledTimes(2);
  });
});
