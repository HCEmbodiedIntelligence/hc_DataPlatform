import type { ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import type { DashboardCoverage } from '../../../features/dashboard/types';
import { DataTable } from '../../../shared/ui';

type CoverageCell = DashboardCoverage['cells'][number];

export function DashboardCoverageTable({ coverage }: Readonly<{ coverage: DashboardCoverage }>) {
  const columns = useMemo<readonly ColumnDef<CoverageCell, unknown>[]>(
    () => [
      { id: 'robotGroup', header: '机器人组', cell: ({ row }) => row.original.robotGroupId },
      { id: 'task', header: '任务', cell: ({ row }) => row.original.taskId },
      {
        id: 'coverage',
        header: '覆盖率',
        cell: ({ row }) => row.original.ratio === null ? '无样本' : `${Math.round(row.original.ratio * 1_000) / 10}%`,
      },
    ],
    [],
  );

  return (
    <DataTable
      data={coverage.cells}
      columns={columns}
      getRowId={(cell) => `${cell.robotGroupId}:${cell.taskId}`}
      caption="机器人组与任务覆盖率"
    />
  );
}
