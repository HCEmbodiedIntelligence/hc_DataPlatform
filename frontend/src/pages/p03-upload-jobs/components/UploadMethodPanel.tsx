import { Input, Radio } from "antd";
import {
  CloudUpload,
  FileJson2,
  FolderOpen,
  Link2,
  LoaderCircle,
  LockKeyhole,
} from "lucide-react";
import styles from "../styles.module.css";

export type UploadSourceChoice =
  | "BROWSER_MULTIPART"
  | "OBJECT_STORAGE_REFERENCE";
export type BrowserSelectionMode = "package" | "folder";

export function UploadMethodPanel(props: {
  readonly sourceType: UploadSourceChoice;
  readonly browserSelectionMode: BrowserSelectionMode;
  readonly files: readonly File[];
  readonly objectStorageUri: string;
  readonly disabled: boolean;
  readonly localChecking?: boolean;
  readonly onSourceTypeChange: (value: UploadSourceChoice) => void;
  readonly onFilesChange: (
    files: readonly File[],
    mode: BrowserSelectionMode,
  ) => void;
  readonly onObjectStorageUriChange: (value: string) => void;
}) {
  const manifestName = props.files.find((file) =>
    file.name.toLowerCase().endsWith(".json"),
  )?.name;

  return (
    <section
      className={`${styles.uploadPanel} ${styles.methodPanel} ${styles.selectionPanel}`}
      aria-labelledby="upload-method-heading"
    >
      <header className={styles.selectionHeading}>
        <span className={styles.stepBadge} aria-hidden="true">
          1
        </span>
        <div>
          <h2 id="upload-method-heading">选择采集文件夹</h2>
          <p>
            浏览器会先在本地读取文件名、大小和 Manifest；确认前不会创建任务或联系服务端预检。
          </p>
        </div>
      </header>

      {props.sourceType === "BROWSER_MULTIPART" ? (
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
              props.onFilesChange(
                Array.from(event.target.files ?? []),
                "folder",
              )
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
            <span>支持包含多个独立 Manifest 数据包的多级目录</span>
            <small>文件只在确认上传后开始提交</small>
          </label>

          {props.localChecking ? (
            <p className={styles.localCheckStatus} role="status">
              <LoaderCircle size={14} aria-hidden="true" />
              正在浏览器本地枚举文件并识别 Manifest（本地检查）
            </p>
          ) : null}

          <details className={styles.alternativeMethods}>
            <summary>其他上传方式</summary>
            <Radio.Group
              className={styles.methodChoices}
              value={props.sourceType}
              disabled={props.disabled}
              onChange={(event) =>
                props.onSourceTypeChange(
                  event.target.value as UploadSourceChoice,
                )
              }
              aria-label="上传方式"
            >
              <Radio value="BROWSER_MULTIPART">
                <span className={styles.methodChoiceCopy}>
                  <strong>
                    <CloudUpload size={15} aria-hidden="true" /> 浏览器分片上传
                  </strong>
                </span>
              </Radio>
              <Radio value="OBJECT_STORAGE_REFERENCE">
                <span className={styles.methodChoiceCopy}>
                  <strong>
                    <Link2 size={15} aria-hidden="true" /> 授权对象地址
                  </strong>
                </span>
              </Radio>
            </Radio.Group>
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
              仅选择一个 Manifest 与 RAW/MCAP 文件
            </label>
          </details>
        </div>
      ) : (
        <div className={styles.objectReferenceFields}>
          <Radio.Group
            className={styles.methodChoices}
            value={props.sourceType}
            disabled={props.disabled}
            onChange={(event) =>
              props.onSourceTypeChange(event.target.value as UploadSourceChoice)
            }
            aria-label="上传方式"
          >
            <Radio value="BROWSER_MULTIPART">浏览器采集文件夹</Radio>
            <Radio value="OBJECT_STORAGE_REFERENCE">授权对象地址</Radio>
          </Radio.Group>
          <label htmlFor="object-storage-uri">已授权对象地址</label>
          <Input
            id="object-storage-uri"
            name="object-storage-uri"
            autoComplete="off"
            value={props.objectStorageUri}
            disabled={props.disabled}
            placeholder="s3://受管存储桶/raw/v1/…/recording.mcap"
            onChange={(event) =>
              props.onObjectStorageUriChange(event.target.value)
            }
          />
          <input
            id="object-upload-manifest"
            name="object-upload-manifest"
            className={styles.visuallyHidden}
            type="file"
            accept=".json,application/json"
            disabled={props.disabled}
            onChange={(event) =>
              props.onFilesChange(
                Array.from(event.target.files ?? []),
                "package",
              )
            }
          />
          <label
            htmlFor="object-upload-manifest"
            className={styles.secondaryPicker}
            aria-disabled={props.disabled}
          >
            <FileJson2 size={15} aria-hidden="true" />
            {manifestName ? `Manifest：${manifestName}` : "选择对应 Manifest"}
          </label>
          <p>
            <LockKeyhole size={13} aria-hidden="true" /> 地址不得包含
            AccessKey、临时密钥或签名查询参数。
          </p>
        </div>
      )}
    </section>
  );
}
