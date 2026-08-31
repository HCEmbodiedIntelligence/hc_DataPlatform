import { Alert, Button, Input, Modal } from "antd";
import {
  FileJson2,
  FolderOpen,
  MapPin,
  ShieldAlert,
  Target,
} from "lucide-react";
import type { IngestScope } from "../../../entities/data-source";
import { formatBytes, selectedRelativePath } from "../upload-contract";
import {
  selectionTargetFacts,
  type LocalUploadSelection,
} from "../upload-flow";
import type { LeRobotTargetBinding } from "../lerobot-client";
import styles from "../styles.module.css";
import { useState } from "react";

const IDENTIFIER = /^[A-Za-z0-9._-]{1,128}$/u;

function joined(values: readonly string[]): string {
  if (values.length === 0) return "未识别";
  if (values.length <= 2) return values.join("、");
  return `${values.slice(0, 2).join("、")} 等 ${values.length} 项`;
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
  const targets = selectionTargetFacts(props.selection, props.scope);
  const lerobotBindingValid =
    props.selection.lerobot === null ||
    [datasetId, collectionTaskId, robotId].every((value) =>
      IDENTIFIER.test(value.trim()),
    );
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
            collectionTaskId: collectionTaskId.trim(),
            robotId: robotId.trim(),
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
              {props.selection.lerobot ? " LeRobot 元数据" : " 数据清单"}
            </dt>
            <dd>
              {props.selection.lerobot
                ? "meta/info.json（原始文件）"
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
                LeRobot v3.0 · {props.selection.lerobot.sourceFiles.length}{" "}
                个原始文件 · {props.selection.lerobot.episodeCount} episodes
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
            <>
              <div>
                <dt>目标数据集 ID</dt>
                <dd>
                  <Input
                    aria-label="目标数据集 ID"
                    value={datasetId}
                    status={
                      datasetId && !IDENTIFIER.test(datasetId.trim())
                        ? "error"
                        : undefined
                    }
                    placeholder="dataset-id"
                    onChange={(event) => setDatasetId(event.target.value)}
                  />
                </dd>
              </div>
              <div>
                <dt>采集任务 ID</dt>
                <dd>
                  <Input
                    aria-label="采集任务 ID"
                    value={collectionTaskId}
                    status={
                      collectionTaskId &&
                      !IDENTIFIER.test(collectionTaskId.trim())
                        ? "error"
                        : undefined
                    }
                    placeholder="collection-task-id"
                    onChange={(event) =>
                      setCollectionTaskId(event.target.value)
                    }
                  />
                </dd>
              </div>
              <div>
                <dt>目标机器人 ID</dt>
                <dd>
                  <Input
                    aria-label="目标机器人 ID"
                    value={robotId}
                    status={
                      robotId && !IDENTIFIER.test(robotId.trim())
                        ? "error"
                        : undefined
                    }
                    placeholder="robot-id"
                    onChange={(event) => setRobotId(event.target.value)}
                  />
                </dd>
              </div>
              <div>
                <dt>Raw 写入方式</dt>
                <dd>按原目录逐对象上传；不转 MCAP，不打 ZIP/TAR</dd>
              </div>
            </>
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
      </div>
    </Modal>
  );
}
