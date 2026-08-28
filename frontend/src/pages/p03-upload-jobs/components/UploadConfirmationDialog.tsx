import { Alert, Button, Modal } from "antd";
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
import styles from "../styles.module.css";

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
  readonly onConfirm: () => void;
}) {
  const targets = selectionTargetFacts(props.selection, props.scope);
  const blocked =
    !props.canManage ||
    !props.online ||
    props.selection.problems.length > 0 ||
    props.selection.units.length === 0;

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
          <Button type="primary" disabled={blocked} onClick={props.onConfirm}>
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
              <FileJson2 size={14} aria-hidden="true" /> 数据清单
            </dt>
            <dd>
              {props.selection.manifestFiles.length === 0
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
          <div>
            <dt>RAW/MCAP 文件</dt>
            <dd>{props.selection.rawFiles.length} 个</dd>
          </div>
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
        </dl>
      </div>
    </Modal>
  );
}
