import { useRef, useState } from "react";
import { Alert, Button, Input, Modal, Radio } from "antd";
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
  const [recording, setRecording] = useState("");
  const [uri, setUri] = useState("");
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [busy, setBusy] = useState<"inspect" | "commit" | null>(null);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [robotId, setRobotId] = useState("");
  const [mode, setMode] = useState<"PROCESS" | "STORE_ONLY">("PROCESS");
  const capabilities = useLeRobotCapabilities(
    props.scope,
    inventory?.source_format === "LEROBOT_V3" ? inventory.info : null,
    open && !props.disabled,
  );
  const processSupported =
    inventory?.source_format === "MCAP" ||
    capabilities.data?.processing_supported === true;
  const processing = processSupported && mode === "PROCESS";
  const target = datasetId;
  const blocked =
    props.disabled ||
    busy !== null ||
    !inventory ||
    (inventory?.archive_members === true && !recording) ||
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
        setRecording("");
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
        await request({
          method: "POST",
          path: `${root}/oss:commit`,
          scope: props.scope,
          scopeMode: "explicit",
          body: {
            source_uri: inventory.source_uri,
            ...(inventory.archive_members ? { recording_path: recording } : {}),
            inventory_sha256: inventory.inventory_sha256,
            dataset_id: target,
            collection_task_id: taskId,
            robot_id: robotId,
            processing_mode: processing ? "PROCESS" : "STORE_ONLY",
            processing_capability_revision:
              capabilities.data?.capability_revision,
          },
        });
        await queryClient.invalidateQueries();
        setOpen(false);
        setInventory(null);
        props.onImported();
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "OSS 导入失败，请重试。",
      );
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
        width={720}
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
              确认导入
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
            <p>导入并处理或仅登记原始数据，都会保留任务和机器人关联。</p>
            <dl className={styles.confirmFacts}>
              <ProcessingTargetFields
                scope={props.scope}
                datasetId={datasetId}
                collectionTaskId={taskId}
                robotId={robotId}
                onDatasetIdChange={setDatasetId}
                onCollectionTaskIdChange={setTaskId}
                onRobotIdChange={setRobotId}
                sourceKind={inventory?.archive && !inventory.archive_members ? "oss-archive" : "oss"}
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
            已配置的只读源 ZIP 支持按录制导入，配套接管标记随录制一起读取。其他 ZIP 解压限 2 GiB。
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
              {inventory.archive_members && (
                <label>选择录制
                  <select value={recording} disabled={!!busy} onChange={e => setRecording(e.target.value)}>
                    <option value="">请选择一条录制</option>
                    {inventory.files.filter(f => f.path.toLowerCase().endsWith(".mcap")).map(f =>
                      <option key={f.path} value={f.path}>{f.path}</option>)}
                  </select>
                </label>
              )}
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
              {processSupported ? (
                <Radio.Group
                  aria-label="OSS 导入处理方式"
                  value={mode}
                  disabled={!!busy}
                  onChange={(event) => setMode(event.target.value)}
                  options={[
                    { label: "导入并处理", value: "PROCESS" },
                    { label: "仅登记原始数据", value: "STORE_ONLY" },
                  ]}
                />
              ) : (
                <p>
                  该数据按现有格式能力登记原始来源，处理能力与本地上传一致。
                </p>
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
          {busy === "commit" && (
            <p role="status">
              正在校验并登记原始数据，耗时取决于文件大小和 OSS
              读取速度。请等待完成。
            </p>
          )}
        </div>
      </Modal>
    </>
  );
}
