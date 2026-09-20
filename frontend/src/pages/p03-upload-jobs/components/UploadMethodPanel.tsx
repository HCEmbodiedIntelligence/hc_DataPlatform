import { FileJson2, FolderOpen, LoaderCircle } from "lucide-react";
import styles from "../styles.module.css";

export type BrowserSelectionMode = "package" | "folder" | "raw";

export function UploadMethodPanel(props: {
  readonly disabled: boolean;
  readonly scopeRequired?: boolean;
  readonly localChecking?: boolean;
  readonly onFilesChange: (
    files: readonly File[],
    mode: BrowserSelectionMode,
  ) => void;
  readonly onScopeRequired?: () => void;
}) {
  return (
    <section
      className={`${styles.uploadPanel} ${styles.methodPanel} ${styles.selectionPanel}`}
      aria-labelledby="upload-method-heading"
      data-scope-required={props.scopeRequired || undefined}
      onClickCapture={(event) => {
        if (!props.scopeRequired) return;
        event.preventDefault();
        event.stopPropagation();
        props.onScopeRequired?.();
      }}
    >
      <header className={styles.selectionHeading}>
        <span className={styles.stepBadge} aria-hidden="true">
          1
        </span>
        <div>
          <h2 id="upload-method-heading">选择采集文件夹</h2>
          <p>
            支持 MCAP、LeRobot v3 和 ROS
            bag。原始视频与数据原样保存，确认前不会上传文件。
          </p>
        </div>
      </header>

      <div className={styles.packagePicker}>
        <input
          id="browser-upload-folder"
          name="browser-upload-folder"
          className={styles.visuallyHidden}
          type="file"
          multiple
          disabled={props.disabled}
          ref={(element) => {
            element?.setAttribute("webkitdirectory", "");
            element?.setAttribute("directory", "");
          }}
          onChange={(event) =>
            props.onFilesChange(Array.from(event.target.files ?? []), "folder")
          }
        />
        <label
          htmlFor="browser-upload-folder"
          className={styles.folderPickerLabel}
          aria-disabled={props.disabled}
        >
          <span className={styles.folderPickerIcon} aria-hidden="true">
            <FolderOpen size={30} />
          </span>
          <strong>选择采集文件夹</strong>
          <span>保留原目录结构和文件内容</span>
          <small>保留原始文件，按配置自动质检、对齐并进入标注</small>
          <small>文件只在确认上传后开始提交</small>
        </label>

        {props.localChecking ? (
          <p className={styles.localCheckStatus} role="status">
            <LoaderCircle size={14} aria-hidden="true" />
            正在浏览器本地枚举文件并识别数据清单（本地检查）
          </p>
        ) : null}

        <details className={styles.alternativeMethods}>
          <summary>选择 MCAP、ROS bag 或带清单的数据包</summary>
          <input
            id="browser-upload-package"
            name="browser-upload-package"
            className={styles.visuallyHidden}
            type="file"
            accept=".bag,.db3,.yaml,.json,.mcap,application/octet-stream"
            multiple
            disabled={props.disabled}
            onChange={(event) =>
              props.onFilesChange(
                Array.from(event.target.files ?? []),
                "folder",
              )
            }
          />
          <label
            htmlFor="browser-upload-package"
            className={styles.secondaryPicker}
            aria-disabled={props.disabled}
          >
            <FileJson2 size={15} aria-hidden="true" />
            选择原始文件（ROS2 bag 请选择包含 metadata.yaml 的完整目录）
          </label>
        </details>
      </div>
    </section>
  );
}
