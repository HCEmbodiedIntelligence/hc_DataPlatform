import { supportsLeRobotProcessing } from "../processing-profile";
import { Alert, Button, Modal, Select, Radio } from "antd";
import {
  FileJson2,
  FolderOpen,
  MapPin,
  ShieldAlert,
  Target,
} from "lucide-react";
import { useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { useDatasetsQuery } from "../../../features/datasets/api/hooks";
import { formatBytes, selectedRelativePath } from "../upload-contract";
import {
  selectionTargetFacts,
  type LocalUploadSelection,
} from "../upload-flow";
import type { LeRobotTargetBinding } from "../lerobot-client";
import styles from "../styles.module.css";
import { ProcessingTargetFields } from "./ProcessingTargetFields";
import {
  ProcessingLabels,
  useProcessingConfiguration,
} from "./ProcessingLabels";

function joined(values: readonly string[]): string {
  if (values.length === 0) return "未识别";
  if (values.length <= 2) return values.join("、");
  return `${values.slice(0, 2).join("、")} 等 ${values.length} 项`;
}

function OriginalDatasetField(props: {
  readonly datasetId: string;
  readonly onChange: (id: string) => void;
}) {
  const [search, setSearch] = useState("");
  const datasets = useDatasetsQuery({
    q: search || undefined,
    limit: 100,
    sort: "activityDesc",
  });
  return (
    <div>
      <dt>目标数据集</dt>
      <dd>
        <Select
          aria-label="目标数据集 ID"
          className={styles.confirmTargetSelect}
          showSearch
          filterOption={false}
          onSearch={setSearch}
          loading={datasets.isPending}
          value={props.datasetId || undefined}
          options={(datasets.data?.items ?? []).map((item) => ({
            value: item.datasetId,
            label: item.name,
          }))}
          placeholder="选择数据集"
          onChange={props.onChange}
          notFoundContent={
            datasets.isError ? "数据集加载失败" : "暂无数据集，请先创建数据集"
          }
        />
        {datasets.isError ? (
          <Button onClick={() => void datasets.refetch()}>重新加载</Button>
        ) : null}
        <small className={styles.confirmTargetHint}>
          原始文件保存后即可查看和下载。
        </small>
      </dd>
    </div>
  );
}

export function UploadConfirmationDialog(props: {
  readonly open: boolean;
  readonly selection: LocalUploadSelection;
  readonly scope: IngestScope;
  readonly canManage: boolean;
  readonly online: boolean;
  readonly onCancel: () => void;
  readonly onConfirm: (binding: LeRobotTargetBinding | null) => void;
}) {
  const [datasetId, setDatasetId] = useState("");
  const [collectionTaskId, setCollectionTaskId] = useState("");
  const [robotId, setRobotId] = useState("");
  const supportsProcessing =
    props.selection.lerobot?.format === "lerobot" &&
    supportsLeRobotProcessing(props.selection.lerobot.info);
  const [processingMode, setProcessingMode] = useState<
    "PROCESS" | "STORE_ONLY"
  >(supportsProcessing ? "PROCESS" : "STORE_ONLY");
  const processing = supportsProcessing && processingMode === "PROCESS";
  const configuration = useProcessingConfiguration(
    props.scope,
    datasetId,
    processing,
  );
  const targets = selectionTargetFacts(props.selection, props.scope);
  const lerobotBindingValid =
    props.selection.lerobot === null ||
    (Boolean(datasetId.trim()) &&
      (!processing ||
        Boolean(
          collectionTaskId && robotId && configuration.data?.configured,
        )));
  const blocked =
    !props.canManage ||
    !props.online ||
    props.selection.problems.length > 0 ||
    (props.selection.units.length === 0 && !props.selection.lerobot) ||
    !lerobotBindingValid;
  const confirm = () =>
    props.onConfirm(
      props.selection.lerobot
        ? {
            datasetId: datasetId.trim(),
            collectionTaskId: processing ? collectionTaskId : null,
            robotId: processing ? robotId : null,
            processingMode: processing ? "PROCESS" : "STORE_ONLY",
          }
        : null,
    );

  return (
    <Modal
      open={props.open}
      title="确认上传"
      width={720}
      destroyOnHidden
      mask={{ closable: false }}
      onCancel={props.onCancel}
      footer={
        <>
          <Button onClick={props.onCancel}>取消</Button>
          <Button type="primary" disabled={blocked} onClick={confirm}>
            确认上传
          </Button>
        </>
      }
    >
      <div className={styles.confirmDialogBody}>
        {supportsProcessing ? (
          <Radio.Group
            value={processingMode}
            onChange={(e) => setProcessingMode(e.target.value)}
            options={[
              { value: "PROCESS", label: "自动质检、对齐并进入标注" },
              { value: "STORE_ONLY", label: "仅存档，稍后处理" },
            ]}
          />
        ) : null}
        {props.selection.problems.length > 0 ? (
          <Alert
            type="error"
            showIcon
            title="本地检查未通过"
            description={
              <ul className={styles.confirmProblemList}>
                {props.selection.problems.map((item, index) => (
                  <li
                    key={`${item.code}-${item.fileName ?? "selection"}-${index}`}
                  >
                    <code>{item.code}</code>
                    {item.fileName ? <span>{item.fileName}</span> : null}
                    <p>{item.detail}</p>
                  </li>
                ))}
              </ul>
            }
          />
        ) : null}

        {!props.canManage ? (
          <Alert
            type="warning"
            showIcon
            icon={<ShieldAlert size={18} />}
            title="缺少上传权限"
            description="当前账号既没有 uploader 上传能力，也不是当前项目管理员，不能创建上传任务。请联系项目管理员授权。"
          />
        ) : !props.online ? (
          <Alert
            type="warning"
            showIcon
            title="网络未连接"
            description="恢复网络后才能确认并提交服务端预检。"
          />
        ) : null}

        <dl className={styles.confirmFacts}>
          <div>
            <dt>
              <FolderOpen size={14} aria-hidden="true" /> 文件夹
            </dt>
            <dd>{props.selection.folderName}</dd>
          </div>
          <div>
            <dt>
              <FileJson2 size={14} aria-hidden="true" />
              {props.selection.lerobot ? " 原始文件" : " 数据清单"}
            </dt>
            <dd>
              {props.selection.lerobot
                ? props.selection.lerobot.format === "lerobot"
                  ? "meta/info.json（原始文件）"
                  : "按原目录保存"
                : props.selection.manifestFiles.length === 0
                  ? "未找到"
                  : props.selection.manifestFiles
                      .slice(0, 3)
                      .map((file) => selectedRelativePath(file) ?? file.name)
                      .join("、")}
              {props.selection.manifestFiles.length > 3
                ? ` 等 ${props.selection.manifestFiles.length} 个`
                : ""}
            </dd>
          </div>
          {props.selection.lerobot ? (
            <div>
              <dt>原始格式</dt>
              <dd>
                {props.selection.lerobot.format.toUpperCase()} ·{" "}
                {props.selection.lerobot.sourceFiles.length} 个原始文件
              </dd>
            </div>
          ) : (
            <div>
              <dt>RAW/MCAP 文件</dt>
              <dd>{props.selection.rawFiles.length} 个</dd>
            </div>
          )}
          <div>
            <dt>总文件大小</dt>
            <dd>
              {props.selection.sourceType === "OBJECT_STORAGE_REFERENCE"
                ? `${formatBytes(props.selection.declaredRawBytes)}（数据清单声明）`
                : formatBytes(props.selection.totalLocalBytes)}
            </dd>
          </div>
          <div>
            <dt>
              <Target size={14} aria-hidden="true" /> 项目
            </dt>
            <dd>{targets.projectId}</dd>
          </div>
          <div>
            <dt>
              <MapPin size={14} aria-hidden="true" /> 区域
            </dt>
            <dd>{targets.regionCode}</dd>
          </div>
          {props.selection.lerobot ? (
            processing ? (
              <ProcessingTargetFields
                scope={props.scope}
                datasetId={datasetId}
                collectionTaskId={collectionTaskId}
                robotId={robotId}
                onDatasetIdChange={setDatasetId}
                onCollectionTaskIdChange={setCollectionTaskId}
                onRobotIdChange={setRobotId}
              />
            ) : (
              <OriginalDatasetField
                datasetId={datasetId}
                onChange={setDatasetId}
              />
            )
          ) : (
            <>
              <div>
                <dt>采集任务</dt>
                <dd>{joined(targets.taskIds)}</dd>
              </div>
              <div>
                <dt>目标机器人</dt>
                <dd>{joined(targets.robotIds)}</dd>
              </div>
              <div>
                <dt>数据包</dt>
                <dd>{joined(targets.packageIds)}</dd>
              </div>
              <div>
                <dt>正式上传单元</dt>
                <dd>{props.selection.units.length} 个 RAW/MCAP 文件任务</dd>
              </div>
            </>
          )}
        </dl>
        {processing ? (
          <ProcessingLabels scope={props.scope} datasetId={datasetId} />
        ) : null}
      </div>
    </Modal>
  );
}
