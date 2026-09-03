import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type OnChangeFn,
  type SortingState,
} from '@tanstack/react-table';
import { useMemo, type ReactNode } from 'react';

export interface StandardTableProps<TData> {
  data: readonly TData[];
  columns: readonly ColumnDef<TData, unknown>[];
  getRowId: (row: TData) => string;
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  caption?: string;
  empty?: ReactNode;
  loading?: boolean;
}

export function StandardTable<TData>({
  data,
  columns,
  getRowId,
  sorting = [],
  onSortingChange,
  caption = '数据表格',
  empty,
  loading = false,
}: StandardTableProps<TData>) {
  const tableData = useMemo(() => [...data], [data]);
  const tableColumns = useMemo(() => [...columns], [columns]);
  const table = useReactTable({
    data: tableData,
    columns: tableColumns,
    getRowId,
    state: { sorting },
    ...(onSortingChange ? { onSortingChange } : {}),
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualFiltering: true,
    manualPagination: true,
  });
  return (
    <div className="standard-table-wrap" aria-busy={loading}>
      <table className="standard-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const sortable = header.column.getCanSort();
                const direction = header.column.getIsSorted();
                return (
                  <th key={header.id} scope="col" aria-sort={direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : sortable ? 'none' : undefined}>
                    {header.isPlaceholder ? null : sortable ? (
                      <button type="button" onClick={header.column.getToggleSortingHandler()}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden="true">{direction === 'asc' ? ' ↑' : direction === 'desc' ? ' ↓' : ' ↕'}</span>
                      </button>
                    ) : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!loading && data.length === 0 ? <div className="standard-table__empty">{empty}</div> : null}
    </div>
  );
}
