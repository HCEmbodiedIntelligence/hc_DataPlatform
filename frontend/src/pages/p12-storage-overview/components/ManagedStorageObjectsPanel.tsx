import { Alert, Button, Input, Modal, Space } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, RotateCcw, Snowflake, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import {
  useAuthorizeStorageObjectDownload,
  useManagedStorageObjects,
  useRestoreStorageObject,
  useTransitionStorageObject,
  useTrashStorageObject,
  type CapacityScope,
  type ManagedStorageObject,
} from "../../../features/storage-overview/capacity-api";
import { isDomainError } from "../../../shared/api/domain-error";
import { useCapabilities } from "../../../shared/auth/use-capabilities";
import { DataTable, PageState, StatusTag } from "../../../shared/ui";
import styles from "../styles.module.css";

type ObjectAction = "TRASH" | "RESTORE" | "ARCHIVE" | "TRANSITION_TO_COLD";

const actionLabels: Record<ObjectAction, string> = {
  TRASH: "移入回收站",
  RESTORE: "恢复对象",
  ARCHIVE: "归档对象",
  TRANSITION_TO_COLD: "迁移到冷存储",
};

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

function openDownload(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener noreferrer";
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

export function ManagedStorageObjectsPanel({
  scope,
  enabled,
}: Readonly<{
  scope: CapacityScope | null;
  enabled: boolean;
}>) {
  const capabilities = useCapabilities();
  const canManage = capabilities.has("storage.object.manage");
  const [cursor, setCursor] = useState<string | undefined>();
  const [pendingAction, setPendingAction] = useState<{
    action: ObjectAction;
    object: ManagedStorageObject;
  } | null>(null);
  const [reason, setReason] = useState("");
  const objects = useManagedStorageObjects(scope, cursor, 25, enabled);
  const download = useAuthorizeStorageObjectDownload();
  const trash = useTrashStorageObject();
  const restore = useRestoreStorageObject();
  const transition = useTransitionStorageObject();
  const busy =
    download.isPending ||
    trash.isPending ||
    restore.isPending ||
    transition.isPending;
  const mutationError =
    download.error ?? trash.error ?? restore.error ?? transition.error;

  const performAction = () => {
    if (!pendingAction || reason.trim().length < 3) return;
    const intent = {
      objectId: pendingAction.object.object_id,
      etag: pendingAction.object.etag,
      idempotencyKey: crypto.randomUUID(),
      reason: reason.trim(),
    };
    const close = { onSuccess: () => setPendingAction(null) };
    if (pendingAction.action === "TRASH") trash.mutate(intent, close);
    if (pendingAction.action === "RESTORE") restore.mutate(intent, close);
    if (
      pendingAction.action === "ARCHIVE" ||
      pendingAction.action === "TRANSITION_TO_COLD"
    ) {
      transition.mutate(
        { ...intent, transitionAction: pendingAction.action },
        close,
      );
    }
  };

  const columns = useMemo<readonly ColumnDef<ManagedStorageObject, unknown>[]>(
    () => [
      {
        id: "object",
        header: "对象 / ID",
        size: 164,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <strong>{row.original.display_key}</strong>
            <code>{row.original.object_id}</code>
          </span>
        ),
      },
      {
        id: "facts",
        header: "容量 / 角色",
        size: 118,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <span>{formatBytes(row.original.physical_bytes)}</span>
            <small>
              {row.original.business_category} · {row.original.object_role}
            </small>
          </span>
        ),
      },
      {
        id: "placement",
        header: "层级 / 状态",
        size: 106,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <StatusTag
              status={row.original.status}
              label={row.original.status}
              tone={
                row.original.status === "ACTIVE"
                  ? "success"
                  : row.original.status === "FAILED"
                    ? "danger"
                    : "warning"
              }
            />
            <small>{row.original.storage_tier}</small>
          </span>
        ),
      },
      {
        id: "protection",
        header: "保护",
        size: 116,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <span>{row.original.active_reference_count} 个活跃引用</span>
            <small>
              {row.original.legal_hold || row.original.governance_hold
                ? "存在保留锁"
                : row.original.retention_until
                  ? `保留至 ${new Date(row.original.retention_until).toLocaleDateString("zh-CN")}`
                  : "无额外保留锁"}
            </small>
          </span>
        ),
      },
      {
        id: "actions",
        header: "安全操作",
        size: 250,
        cell: ({ row }) => {
          const value = row.original;
          const allowed = new Set(value.allowed_actions);
          return (
            <Space size={4} wrap>
              <Button
                size="small"
                icon={<Download aria-hidden="true" size={13} />}
                disabled={!allowed.has("DOWNLOAD") || busy}
                onClick={() =>
                  download.mutate(
                    {
                      objectId: value.object_id,
                      etag: value.etag,
                      reason: "authorize object download",
                      idempotencyKey: crypto.randomUUID(),
                    },
                    { onSuccess: (grant) => openDownload(grant.url) },
                  )
                }
              >
                下载
              </Button>
              {allowed.has("RESTORE") ? (
                <Button
                  size="small"
                  icon={<RotateCcw aria-hidden="true" size={13} />}
                  disabled={!canManage || busy}
                  onClick={() => {
                    setReason("");
                    setPendingAction({ action: "RESTORE", object: value });
                  }}
                >
                  恢复
                </Button>
              ) : null}
              {allowed.has("TRANSITION_TO_COLD") ? (
                <Button
                  size="small"
                  icon={<Snowflake aria-hidden="true" size={13} />}
                  disabled={!canManage || busy}
                  onClick={() => {
                    setReason("");
                    setPendingAction({
                      action: "TRANSITION_TO_COLD",
                      object: value,
                    });
                  }}
                >
                  冷存储
                </Button>
              ) : null}
              {allowed.has("ARCHIVE") ? (
                <Button
                  size="small"
                  disabled={!canManage || busy}
                  onClick={() => {
                    setReason("");
                    setPendingAction({ action: "ARCHIVE", object: value });
                  }}
                >
                  归档
                </Button>
              ) : null}
              {allowed.has("TRASH") ? (
                <Button
                  size="small"
                  danger
                  icon={<Trash2 aria-hidden="true" size={13} />}
                  disabled={!canManage || busy}
                  onClick={() => {
                    setReason("");
                    setPendingAction({ action: "TRASH", object: value });
                  }}
                >
                  回收站
                </Button>
              ) : null}
            </Space>
          );
        },
      },
    ],
    [busy, canManage, download],
  );

  return (
    <section
      className={styles.projectCard}
      aria-labelledby="managed-objects-title"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="managed-objects-title">存储对象操作</h3>
        </div>
        <Button
          size="small"
          loading={objects.isFetching}
          onClick={() => void objects.refetch()}
        >
          刷新
        </Button>
      </div>
      {objects.isPending ? (
        <PageState state="loading" label="存储对象" />
      ) : objects.isError ? (
        <PageState
          state="error"
          label="存储对象"
          onRetry={() => void objects.refetch()}
        />
      ) : objects.data?.items.length ? (
        <>
          <DataTable
            data={objects.data.items}
            columns={columns}
            getRowId={(item) => item.object_id}
            caption="可治理存储对象"
          />
          <Space className={styles.formActions}>
            <Button
              size="small"
              disabled={
                !objects.data.page_info.has_previous_page ||
                !objects.data.page_info.start_cursor
              }
              onClick={() =>
                setCursor(objects.data?.page_info.start_cursor ?? undefined)
              }
            >
              上一组
            </Button>
            <Button
              size="small"
              disabled={
                !objects.data.page_info.has_next_page ||
                !objects.data.page_info.end_cursor
              }
              onClick={() =>
                setCursor(objects.data?.page_info.end_cursor ?? undefined)
              }
            >
              下一组
            </Button>
          </Space>
        </>
      ) : (
        <PageState
          state="empty"
          label="存储对象"
          description="当前项目还没有已登记、可治理的对象。"
        />
      )}
      {mutationError ? (
        <Alert
          type="error"
          showIcon
          title="对象操作未完成"
          description={
            isDomainError(mutationError)
              ? mutationError.message
              : "对象状态未发生乐观变化，请重试。"
          }
        />
      ) : null}
      <Modal
        open={pendingAction !== null}
        title={pendingAction ? actionLabels[pendingAction.action] : "对象操作"}
        okText="确认执行"
        cancelText="取消"
        okButtonProps={{
          danger: pendingAction?.action === "TRASH",
          disabled: reason.trim().length < 3,
          loading: busy,
        }}
        onOk={performAction}
        onCancel={() => setPendingAction(null)}
      >
        <p>
          对象 <code>{pendingAction?.object.object_id}</code>
          ；服务端会再次检查引用、保留期与锁。
        </p>
        <Input.TextArea
          value={reason}
          maxLength={512}
          rows={3}
          placeholder="填写操作原因（至少 3 个字符）"
          onChange={(event) => setReason(event.target.value)}
        />
      </Modal>
    </section>
  );
}
