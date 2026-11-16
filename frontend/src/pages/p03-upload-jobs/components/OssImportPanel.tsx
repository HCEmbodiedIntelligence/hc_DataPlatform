import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Modal } from "antd";
import { batchKey, type OssBatch } from "./OssBatchRecords";
import { ArchiveRecordingPicker } from "./ArchiveRecordingPicker";
import { CloudDownload } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { ProcessingTargetFields } from "../../../features/ingest/processing/components/ProcessingTargetFields";
import { useLeRobotCapabilities } from "../../../features/ingest/upload/lerobot-capabilities";
import { formatBytes } from "../../../features/ingest/upload/upload-contract";
import styles from "../../../features/ingest/upload/upload.module.css";

const inventoryWire = z.object({
  source_uri: z.string(),
  source_format: z.enum(["MCAP", "LEROBOT_V3", "ROSBAG"]),
  info: z.record(z.string(), z.unknown()),
  inventory_sha256: z.string(),
  total_bytes: z.number(),
  archive_members: z.boolean().optional(),
  archive: z.object({ path: z.string() }).passthrough().optional(),
  files: z.array(z.object({ path: z.string(), size: z.number() })),
});
type Inventory = z.infer<typeof inventoryWire>;

export function OssImportPanel(props: {
  scope: IngestScope;
  disabled: boolean;
  onImported: () => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const requestId = useRef<string | null>(null);
  const [uri, setUri] = useState("");
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [busy, setBusy] = useState<"inspect" | "commit" | null>(null);
  const busyRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [error, setError] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [robotId, setRobotId] = useState("");
  const capabilities = useLeRobotCapabilities(
    props.scope,
    inventory?.source_format === "LEROBOT_V3" ? inventory.info : null,
    open && !props.disabled,
  );
  const processSupported =
    inventory?.source_format === "MCAP" ||
    (inventory?.source_format === "LEROBOT_V3" &&
      capabilities.data?.processing_supported === true);
  const target = datasetId;
  const blocked =
    props.disabled ||
    busy !== null ||
    !inventory ||
    !processSupported ||
    (inventory.archive_members && selected.size === 0) ||
    !target ||
    !taskId ||
    !robotId ||
    (inventory.source_format === "LEROBOT_V3" &&
      (capabilities.isFetching || !capabilities.data?.storage_supported));
  const root = `/projects/${encodeURIComponent(props.scope.projectId)}/regions/${encodeURIComponent(props.scope.regionCode)}/lerobot-imports`;

  async function run(action: "inspect" | "commit") {
    if (busyRef.current || props.disabled || (action === "commit" && blocked))
      return;
    busyRef.current = true;
    setBusy(action);
    setError("");
    try {
      if (action === "inspect") {
        setSelected(new Set());
        setInventory(null);
        setInventory(
          inventoryWire.parse(
            await request({
              method: "POST",
              path: `${root}/oss:inspect`,
              scope: props.scope,
              scopeMode: "explicit",
              cache: "no-store",
              body: { source_uri: uri.trim() },
            }),
          ),
        );
      } else if (inventory) {
        requestId.current ??= crypto.randomUUID();
        const receipt = z
          .object({ batch_id: z.string(), batch_number: z.number().int().positive().nullish(), total: z.number().positive() })
          .parse(
            await request({
              method: "POST",
              path: `${root}/oss:batches`,
              scope: props.scope,
              scopeMode: "explicit",
              body: {
                request_id: requestId.current,
                source_uri: inventory.source_uri,
                inventory_sha256: inventory.inventory_sha256,
                dataset_id: target,
                collection_task_id: taskId,
                robot_id: robotId,
                processing_mode: "PROCESS",
                processing_capability_revision:
                  capabilities.data?.capability_revision,
                recording_paths: inventory.archive_members
                  ? [...selected].sort()
                  : null,
              },
            }),
          );
        requestId.current = null;
        const batch: OssBatch = {
          ...receipt,
          status: "PENDING",
          submitted: 0,
          failed: 0,
          source_name:
            inventory.source_uri.replace(/\/+$/, "").split("/").pop() ||
            inventory.source_uri,
        };
        queryClient.setQueryData<OssBatch[]>(batchKey(props.scope), (old) => [
          batch,
          ...(old ?? []).filter((row) => row.batch_id !== batch.batch_id),
        ]);
        void queryClient.invalidateQueries({ queryKey: batchKey(props.scope) });
        setOpen(false);
        setInventory(null);
        if (mounted.current) props.onImported();
      }
    } catch (cause) {
      const message =
        cause instanceof Error ? cause.message : "OSS 导入失败，请重试。";
      setError(message);
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }
  return (
    <>
      <Button
        icon={<CloudDownload size={16} />}
        disabled={props.disabled}
        onClick={() => setOpen(true)}
      >
        从 OSS 导入
      </Button>
      <Modal
        open={open}
        title="从 OSS 导入"
        width={760}
        centered
        styles={{
          body: { maxHeight: "calc(100dvh - 150px)", overflowY: "auto" },
        }}
        mask={{ closable: !busy }}
        closable={!busy}
        onCancel={() => {
          if (!busy) setOpen(false);
        }}
        footer={
          <>
            <Button disabled={!!busy} onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              type="primary"
              disabled={blocked}
              loading={busy === "commit"}
              onClick={() => void run("commit")}
            >
              确认导入并处理
              {inventory?.archive_members && selected.size
                ? `（${selected.size} 条）`
                : ""}
            </Button>
          </>
        }
      >
        <div className={styles.confirmDialogBody}>
          <p>
            直接使用已有 OSS 文件或 ZIP
            压缩包，无需下载到本地再上传。先关联采集任务，数据集由任务自动确定。
          </p>
          <fieldset disabled={!!busy} style={{ border: 0, padding: 0 }}>
            <legend>关联采集任务</legend>
            <dl className={styles.confirmFacts}>
              <ProcessingTargetFields
                scope={props.scope}
                datasetId={datasetId}
                collectionTaskId={taskId}
                robotId={robotId}
                onDatasetIdChange={setDatasetId}
                onCollectionTaskIdChange={setTaskId}
                onRobotIdChange={setRobotId}
                sourceKind={
                  inventory?.archive && !inventory.archive_members
                    ? "oss-archive"
                    : "oss"
                }
                disabled={!!busy}
              />
            </dl>
          </fieldset>
          <label htmlFor="oss-source-uri">OSS 文件或目录地址</label>
          <Input
            id="oss-source-uri"
            value={uri}
            disabled={!!busy}
            placeholder="oss://bucket/采集目录/ 或 oss://bucket/采集数据.zip"
            onChange={(event) => {
              setUri(event.target.value);
              setInventory(null);
              setError("");
            }}
          />
          <small>
            录制 ZIP 可多选 MCAP，配套接管标记会随录制读取。其他 ZIP 解压限 2
            GiB。
          </small>
          <Button
            disabled={props.disabled || !!busy || !uri.trim()}
            loading={busy === "inspect"}
            onClick={() => void run("inspect")}
          >
            检查 OSS 数据
          </Button>
          {error && (
            <Alert
              type="error"
              showIcon
              title="OSS 导入未完成"
              description={error}
            />
          )}
          {inventory && (
            <>
              <p role="status">
                已识别 {inventory.source_format} · {inventory.files.length}{" "}
                个文件 · {formatBytes(inventory.total_bytes)}
              </p>
              {inventory.archive_members ? (
                <ArchiveRecordingPicker
                  files={inventory.files}
                  selected={selected}
                  onChange={setSelected}
                  disabled={!!busy}
                />
              ) : (
                <>
                  <ul>
                    {inventory.files.slice(0, 8).map((file) => (
                      <li key={file.path}>
                        {file.path} · {formatBytes(file.size)}
                      </li>
                    ))}
                  </ul>
                  {inventory.files.length > 8 && (
                    <small>
                      另外还有 {inventory.files.length - 8} 个文件。
                    </small>
                  )}
                </>
              )}
              {capabilities.isError && (
                <Alert
                  type="error"
                  title="处理能力检查失败"
                  action={
                    <Button onClick={() => void capabilities.refetch()}>
                      重试
                    </Button>
                  }
                />
              )}
              {!processSupported &&
                !capabilities.isFetching &&
                !capabilities.isError && (
                  <Alert
                    type="warning"
                    showIcon
                    title="当前数据格式不支持在这里导入并处理。"
                  />
                )}
              <Alert
                type="info"
                showIcon
                title={
                  inventory.archive && !inventory.archive_members
                    ? "ZIP 将自动解压导入"
                    : "请保留 OSS 原始文件"
                }
                description={
                  inventory.archive && !inventory.archive_members
                    ? "确认导入后，解压内容将保存到平台原始数据目录，并关联所选采集任务；源 ZIP 不会被修改。"
                    : "处理、重处理和导出会读取这些对象。请勿删除或覆盖原文件；平台不会复制一份永久原始数据。"
                }
              />
            </>
          )}
          {busy === "commit" && <p role="status">正在提交后台任务…</p>}
        </div>
      </Modal>
    </>
  );
}
