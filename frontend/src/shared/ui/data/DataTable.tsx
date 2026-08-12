import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnFiltersState,
  type OnChangeFn,
  type Row,
  type RowSelectionState,
  type SortingState,
} from '@tanstack/react-table';
import { Table, type TableProps } from 'antd';
import { useMemo, type ReactNode } from 'react';
import { validateDataTableRows } from './row-contract';

export type DataTableState = 'ready' | 'loading' | 'empty' | 'error';

export interface DataTableSelection<TData> {
  selectedRowIds: readonly string[];
  onSelectedRowIdsChange: (rowIds: readonly string[]) => void;
  isRowSelectable?: (row: TData) => boolean;
  getRowSelectionLabel?: (row: TData) => string;
  selectAllLabel?: string;
}

export interface DataTableProps<TData> {
  data: readonly TData[];
  columns: readonly ColumnDef<TData, unknown>[];
  getRowId: (row: TData) => string;
  caption?: string;
  state?: DataTableState;
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  columnFilters?: ColumnFiltersState;
  onColumnFiltersChange?: OnChangeFn<ColumnFiltersState>;
  selection?: DataTableSelection<TData>;
  empty?: ReactNode;
  error?: ReactNode;
  loading?: ReactNode;
}

function stateContent(state: DataTableState, content: ReactNode | undefined): ReactNode {
  if (content !== undefined) return content;
  if (state === 'error') return <span role="alert">数据加载失败</span>;
  if (state === 'loading') return <span role="status">正在加载数据</span>;
  return <span>暂无数据</span>;
}

function sortingLabel(columnId: string, direction: false | 'asc' | 'desc'): string {
  if (direction === 'asc') return `${columnId}：升序，激活以切换为降序`;
  if (direction === 'desc') return `${columnId}：降序，激活以取消排序`;
  return `${columnId}：未排序，激活以升序排列`;
}

export function DataTable<TData>({
  data,
  columns,
  getRowId,
  caption = '数据表格',
  state,
  sorting = [],
  onSortingChange,
  columnFilters = [],
  onColumnFiltersChange,
  selection,
  empty,
  error,
  loading,
}: DataTableProps<TData>) {
  const tableData = useMemo(() => validateDataTableRows(data, getRowId), [data, getRowId]);
  const tableColumns = useMemo(() => [...columns], [columns]);
  const rowSelection = useMemo<RowSelectionState>(
    () => Object.fromEntries((selection?.selectedRowIds ?? []).map((rowId) => [rowId, true])),
    [selection?.selectedRowIds],
  );
  const resolvedState = state ?? (tableData.length === 0 ? 'empty' : 'ready');

  const table = useReactTable({
    data: tableData,
    columns: tableColumns,
    getRowId,
    state: { sorting, columnFilters, rowSelection },
    ...(onSortingChange ? { onSortingChange } : {}),
    ...(onColumnFiltersChange ? { onColumnFiltersChange } : {}),
    enableSorting: onSortingChange !== undefined,
    enableRowSelection: selection
      ? (row) => selection.isRowSelectable?.(row.original) ?? true
      : false,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualFiltering: true,
    manualPagination: true,
  });

  const antColumns = table
    .getVisibleLeafColumns()
    .map<NonNullable<TableProps<Row<TData>>['columns']>[number]>((column) => {
      const header = table.getFlatHeaders().find((candidate) => candidate.column.id === column.id);
      const direction = column.getIsSorted();
      const sortable = column.getCanSort();
      const ariaSort =
        direction === 'asc'
          ? ('ascending' as const)
          : direction === 'desc'
            ? ('descending' as const)
            : sortable
              ? ('none' as const)
              : undefined;

      return {
        key: column.id,
        ...(column.columnDef.size === undefined ? {} : { width: column.columnDef.size }),
        title:
          header === undefined || header.isPlaceholder ? null : sortable ? (
            <button
              type="button"
              aria-label={sortingLabel(column.id, direction)}
              onClick={header.column.getToggleSortingHandler()}
            >
              {flexRender(header.column.columnDef.header, header.getContext())}
              <span aria-hidden="true">
                {direction === 'asc' ? ' ↑' : direction === 'desc' ? ' ↓' : ' ↕'}
              </span>
            </button>
          ) : (
            flexRender(header.column.columnDef.header, header.getContext())
          ),
        ...(ariaSort === undefined
          ? {}
          : {
              onHeaderCell: () => ({ 'aria-sort': ariaSort }),
            }),
        render: (_value: unknown, row: Row<TData>) => {
          const cell = row.getVisibleCells().find((candidate) => candidate.column.id === column.id);
          return cell === undefined
            ? null
            : flexRender(cell.column.columnDef.cell, cell.getContext());
        },
      };
    });

  const antRowSelection: TableProps<Row<TData>>['rowSelection'] = selection
    ? {
        selectedRowKeys: [...selection.selectedRowIds],
        preserveSelectedRowKeys: true,
        onChange: (selectedRowKeys) => {
          selection.onSelectedRowIdsChange(selectedRowKeys.map(String));
        },
        getCheckboxProps: (row) => ({
          disabled: !(selection.isRowSelectable?.(row.original) ?? true),
          'aria-label': selection.getRowSelectionLabel?.(row.original) ?? `选择行 ${row.id}`,
        }),
        getTitleCheckboxProps: () => ({
          'aria-label': selection.selectAllLabel ?? '选择当前数据窗口全部行',
        }),
      }
    : undefined;

  const rows =
    resolvedState === 'error' || resolvedState === 'empty' ? [] : table.getRowModel().rows;
  const emptyText =
    resolvedState === 'error'
      ? stateContent('error', error)
      : resolvedState === 'loading'
        ? stateContent('loading', loading)
        : stateContent('empty', empty);

  return (
    <section
      aria-label={caption}
      aria-busy={resolvedState === 'loading'}
      data-pagination-contract="cursor-only"
    >
      <Table<Row<TData>>
        rootClassName="hc-data-table"
        columns={antColumns}
        dataSource={rows}
        rowKey={(row) => row.id}
        rowSelection={antRowSelection}
        loading={resolvedState === 'loading'}
        locale={{ emptyText }}
        pagination={false}
        size="small"
        scroll={{ x: 'max-content' }}
      />
    </section>
  );
}
