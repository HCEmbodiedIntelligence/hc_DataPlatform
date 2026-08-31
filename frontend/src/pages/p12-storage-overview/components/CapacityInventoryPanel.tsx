import { Button, Space } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import {
  useCapacityInventory,
  type CapacityInventoryFact,
  type CapacityScope,
} from "../../../features/storage-overview/capacity-api";
import { DataTable, PageState, StatusTag } from "../../../shared/ui";
import styles from "../styles.module.css";

function formatBytes(value: string): string {
  const bytes = BigInt(value);
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;
  let unit = 0;
  let divisor = 1n;
  while (unit < units.length - 1 && bytes >= divisor * 1024n) {
    divisor *= 1024n;
    unit += 1;
  }
  const tenths = (bytes * 10n + divisor / 2n) / divisor;
  return `${tenths / 10n}${tenths % 10n ? `.${tenths % 10n}` : ""} ${units[unit]}`;
}

const columns: readonly ColumnDef<CapacityInventoryFact, unknown>[] = [
  {
    id: "physical-instance",
    header: "物理实例",
    size: 220,
    cell: ({ row }) => <code>{row.original.physical_instance_id}</code>,
  },
  {
    id: "capacity",
    header: "物理容量",
    size: 96,
    cell: ({ row }) => formatBytes(row.original.physical_bytes),
  },
  {
    id: "category",
    header: "业务分类",
    size: 130,
    cell: ({ row }) => (
      <StatusTag
        status={row.original.business_category ?? row.original.disposition}
        label={row.original.business_category ?? row.original.disposition}
        tone={
          row.original.business_category === "ISSUE_DATA" ? "danger" : "neutral"
        }
      />
    ),
  },
  {
    id: "role",
    header: "对象角色",
    size: 160,
    cell: ({ row }) => row.original.object_role,
  },
  {
    id: "observed-at",
    header: "盘点时间",
    size: 150,
    cell: ({ row }) =>
      new Date(row.original.observed_at).toLocaleString("zh-CN"),
  },
];

export function CapacityInventoryPanel({
  scope,
  snapshotId,
  enabled,
}: Readonly<{
  scope: CapacityScope;
  snapshotId: string;
  enabled: boolean;
}>) {
  const [cursor, setCursor] = useState<string | undefined>();
  const inventory = useCapacityInventory(
    scope,
    snapshotId,
    cursor,
    25,
    enabled,
  );

  const facts = useMemo(() => inventory.data?.items ?? [], [inventory.data]);

  return (
    <section
      className={styles.projectCard}
      aria-labelledby="capacity-inventory-title"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="capacity-inventory-title">容量对象清单</h3>
        </div>
        <Button
          size="small"
          loading={inventory.isFetching}
          onClick={() => void inventory.refetch()}
        >
          刷新
        </Button>
      </div>
      {inventory.isPending ? (
        <PageState state="loading" label="容量对象清单" />
      ) : inventory.isError ? (
        <PageState
          state="error"
          label="容量对象清单"
          onRetry={() => void inventory.refetch()}
        />
      ) : facts.length ? (
        <>
          <DataTable
            data={facts}
            columns={columns}
            getRowId={(item) => item.physical_instance_id}
            caption="固定盘点记录容量对象清单"
          />
          <Space className={styles.formActions}>
            <Button
              size="small"
              disabled={
                !inventory.data?.page_info.has_previous_page ||
                !inventory.data.page_info.start_cursor
              }
              onClick={() =>
                setCursor(inventory.data?.page_info.start_cursor ?? undefined)
              }
            >
              上一页
            </Button>
            <Button
              size="small"
              disabled={
                !inventory.data?.page_info.has_next_page ||
                !inventory.data.page_info.end_cursor
              }
              onClick={() =>
                setCursor(inventory.data?.page_info.end_cursor ?? undefined)
              }
            >
              下一页
            </Button>
          </Space>
        </>
      ) : (
        <PageState
          state="empty"
          label="容量对象清单"
          title="当前盘点中没有对象"
          description="该固定盘点记录没有可展示的物理对象事实。"
        />
      )}
    </section>
  );
}
