import { FileJson2, FolderOpen, LoaderCircle } from "lucide-react";
import styles from "../styles.module.css";

export type BrowserSelectionMode = "package" | "folder";

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
            浏览器会先在本地读取文件名、大小和数据清单；确认前不会创建任务或联系服务端预检。
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
          <span>支持包含多个独立数据清单的数据包多级目录</span>
          <small>文件只在确认上传后开始提交</small>
        </label>

        {props.localChecking ? (
          <p className={styles.localCheckStatus} role="status">
            <LoaderCircle size={14} aria-hidden="true" />
            正在浏览器本地枚举文件并识别数据清单（本地检查）
          </p>
        ) : null}

        <details className={styles.alternativeMethods}>
          <summary>选择单个数据包</summary>
          <input
            id="browser-upload-package"
            name="browser-upload-package"
            className={styles.visuallyHidden}
            type="file"
            accept=".json,.mcap,application/json,application/octet-stream"
            multiple
            disabled={props.disabled}
            onChange={(event) =>
              props.onFilesChange(
                Array.from(event.target.files ?? []),
                "package",
              )
            }
          />
          <label
            htmlFor="browser-upload-package"
            className={styles.secondaryPicker}
            aria-disabled={props.disabled}
          >
            <FileJson2 size={15} aria-hidden="true" />
            选择一个数据清单与 RAW/MCAP 文件
          </label>
        </details>
      </div>
    </section>
  );
}
