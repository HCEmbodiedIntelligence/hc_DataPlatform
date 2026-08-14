import type { ColumnDef } from '@tanstack/react-table';
import { Space, Typography } from 'antd';
import { useMemo } from 'react';
import type { RawObject } from '../../../entities/raw-object';
import { DataTable, StatusTag } from '../../../shared/ui';

function enumText(value: string | { readonly raw: string }): string {
  return typeof value === 'string' ? value : `UNKNOWN (${value.raw})`;
}

export function UploadObjectsTable(props: { readonly objects: readonly RawObject[] }) {
  const columns = useMemo<readonly ColumnDef<RawObject, unknown>[]>(() => [
    {
      id: 'object',
      header: '对象',
      cell: ({ row }) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{row.original.relativePath}</Typography.Text>
          <Typography.Text code>{row.original.id}</Typography.Text>
        </Space>
      ),
    },
    { id: 'size', header: '大小', cell: ({ row }) => `${row.original.sizeBytes} B` },
    {
      id: 'multipart',
      header: 'Multipart',
      cell: ({ row }) => {
        const status = enumText(row.original.multipartStatus);
        return (
          <Space orientation="vertical" size={0}>
            <StatusTag status={status} label={status} known={typeof row.original.multipartStatus === 'string'} />
            {typeof row.original.multipartStatus === 'string' ? null : <Typography.Text>{status}</Typography.Text>}
            <Typography.Text type="secondary">{row.original.completedParts}/{row.original.totalParts ?? '?'}</Typography.Text>
          </Space>
        );
      },
    },
    {
      id: 'multipartEtag',
      header: 'Multipart ETag',
      cell: ({ row }) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text code>{row.original.multipartEtag ?? '—'}</Typography.Text>
          <Typography.Text type="secondary">仅为 Multipart 标识，不等价于内容摘要</Typography.Text>
        </Space>
      ),
    },
    {
      id: 'sha256',
      header: '内容 SHA-256',
      cell: ({ row }) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text code>{row.original.verifiedSha256 ?? row.original.declaredSha256 ?? '—'}</Typography.Text>
          <Typography.Text type="secondary">内容完整性 SHA-256</Typography.Text>
        </Space>
      ),
    },
    {
      id: 'verification',
      header: '校验',
      cell: ({ row }) => {
        const status = enumText(row.original.verificationStatus);
        return (
          <Space size={2}>
            <StatusTag status={status} label={status} known={typeof row.original.verificationStatus === 'string'} />
            {typeof row.original.verificationStatus === 'string' ? null : <Typography.Text>{status}</Typography.Text>}
          </Space>
        );
      },
    },
  ], []);

  return (
    <DataTable
      data={props.objects}
      columns={columns}
      getRowId={(object) => object.id}
      caption="上传对象清单（服务端游标窗口）"
    />
  );
}
