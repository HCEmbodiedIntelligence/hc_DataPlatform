import {
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";
import { filesFromDrop } from "../../../shared/lib/files-from-drop";
import {
  prepareCaptureFiles,
  withRelativePath,
} from "../../../features/ingest/upload/capture-files";
import { FileJson2, FolderOpen, LoaderCircle } from "lucide-react";
import styles from "../../../features/ingest/upload/upload.module.css";

export type BrowserSelectionMode = "package" | "folder" | "raw";

export function UploadMethodPanel(props: {
  readonly disabled: boolean;
  readonly additionalAction?: ReactNode;
  readonly scopeRequired?: boolean;
  readonly localChecking?: boolean;
  readonly onFilesChange: (
    files: readonly File[],
    mode: BrowserSelectionMode,
  ) => Promise<void> | void;
  readonly onScopeRequired?: () => void;
}) {
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const busy = useRef(false);
  const disabled = props.disabled || reading;

  async function select(read: () => Promise<readonly File[]>) {
    if (props.scopeRequired) {
      props.onScopeRequired?.();
      return;
    }
    if (disabled || busy.current) return;
    busy.current = true;
    setReading(true);
    setError("");
    try {
      const files = await prepareCaptureFiles(await read());
      if (!files.length) throw new Error("没有可读取的文件，请重新选择。");
      await props.onFilesChange(files, "folder");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "读取失败，请重新选择文件或文件夹。",
      );
    } finally {
      busy.current = false;
      setReading(false);
    }
  }

  function pick(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    if (files.length) void select(async () => files);
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const transfer = event.dataTransfer;
    void select(async () =>
      (await filesFromDrop(transfer)).map(({ file, relativePath }) =>
        relativePath && relativePath !== file.name
          ? withRelativePath(file, relativePath)
          : file,
      ),
    );
  }

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
          <h2 id="upload-method-heading">选择采集数据</h2>
          <p>
            支持 MCAP、LeRobot v3 和 ROS bag。ZIP 解压后限 2
            GiB，超过时请选择文件夹。
          </p>
        </div>
      </header>

      <div
        className={styles.packagePicker}
        data-dragging={dragging || undefined}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = disabled ? "none" : "copy";
          setDragging(!disabled);
        }}
        onDragLeave={(event) => {
          if (
            !(event.relatedTarget instanceof Node) ||
            !event.currentTarget.contains(event.relatedTarget)
          )
            setDragging(false);
        }}
        onDrop={drop}
      >
        <input
          id="browser-upload-folder"
          name="browser-upload-folder"
          className={styles.visuallyHidden}
          type="file"
          multiple
          disabled={disabled}
          ref={(element) => {
            element?.setAttribute("webkitdirectory", "");
            element?.setAttribute("directory", "");
          }}
          onChange={pick}
        />
        <label
          htmlFor="browser-upload-folder"
          className={styles.folderPickerLabel}
          aria-disabled={disabled}
        >
          <span className={styles.folderPickerIcon} aria-hidden="true">
            <FolderOpen size={30} />
          </span>
          <strong>选择采集文件夹</strong>
          <span>保留原目录结构和文件内容</span>
          <small>也可将 ZIP、原始文件或整个采集文件夹拖到这里</small>
          <small>文件只在确认上传后开始提交</small>
        </label>

        {reading || props.localChecking ? (
          <p className={styles.localCheckStatus} role="status">
            <LoaderCircle size={14} aria-hidden="true" />
            正在本地读取采集数据…
          </p>
        ) : null}

        {error && <p role="alert">{error}</p>}
        <div className={styles.filePickerAction}>
          <input
            id="browser-upload-package"
            name="browser-upload-package"
            className={styles.visuallyHidden}
            type="file"
            accept=".zip,.raw,.bag,.db3,.yaml,.json,.mcap,application/octet-stream"
            multiple
            disabled={disabled}
            onChange={pick}
          />
          <label
            htmlFor="browser-upload-package"
            className={styles.secondaryPicker}
            aria-disabled={disabled}
          >
            <FileJson2 size={15} aria-hidden="true" />
            选择 ZIP 压缩包或原始文件
          </label>
          {props.additionalAction}
        </div>
      </div>
    </section>
  );
}
