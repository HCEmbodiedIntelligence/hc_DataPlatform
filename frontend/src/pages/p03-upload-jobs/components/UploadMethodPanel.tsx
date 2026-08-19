import { Input, Radio, Tag } from "antd";
import {
  CloudUpload,
  FileJson2,
  FolderOpen,
  Link2,
  LockKeyhole,
  MapPin,
  Target,
} from "lucide-react";
import type { ManifestPreflight } from "../formal-client";
import styles from "../styles.module.css";

export type UploadSourceChoice =
  | "BROWSER_MULTIPART"
  | "OBJECT_STORAGE_REFERENCE";

export function UploadMethodPanel(props: {
  readonly sourceType: UploadSourceChoice;
  readonly files: readonly File[];
  readonly objectStorageUri: string;
  readonly projectId: string;
  readonly regionCode: string;
  readonly preflight: ManifestPreflight | null;
  readonly disabled: boolean;
  readonly onSourceTypeChange: (value: UploadSourceChoice) => void;
  readonly onFilesChange: (files: readonly File[]) => void;
  readonly onObjectStorageUriChange: (value: string) => void;
}) {
  const manifestName = props.files.find((file) =>
    file.name.toLowerCase().endsWith(".json"),
  )?.name;
  const rawName = props.files.find((file) =>
    file.name.toLowerCase().endsWith(".mcap"),
  )?.name;

  return (
    <section
      className={`${styles.uploadPanel} ${styles.methodPanel}`}
      aria-labelledby="upload-method-heading"
    >
      <header className={styles.panelHeader}>
        <span className={styles.stepBadge} aria-hidden="true">
          1
        </span>
        <div>
          <h2 id="upload-method-heading">上传方式与目标</h2>
        </div>
      </header>

      <Radio.Group
        className={styles.methodChoices}
        value={props.sourceType}
        disabled={props.disabled}
        onChange={(event) =>
          props.onSourceTypeChange(event.target.value as UploadSourceChoice)
        }
        aria-label="上传方式"
      >
        <Radio value="BROWSER_MULTIPART">
          <span className={styles.methodChoiceCopy}>
            <strong>
              <CloudUpload size={15} aria-hidden="true" /> 浏览器数据包
            </strong>
            <small>直接分片上传 RAW_MCAP，支持暂停和断点续传</small>
          </span>
        </Radio>
        <Radio value="OBJECT_STORAGE_REFERENCE">
          <span className={styles.methodChoiceCopy}>
            <strong>
              <Link2 size={15} aria-hidden="true" /> 授权对象地址
            </strong>
            <small>登记平台已获权读取的 canonical 对象，不粘贴签名 URL</small>
          </span>
        </Radio>
      </Radio.Group>

      {props.sourceType === "BROWSER_MULTIPART" ? (
        <div className={styles.packagePicker}>
          <input
            id="browser-upload-package"
            name="browser-upload-package"
            className={styles.visuallyHidden}
            type="file"
            accept=".json,.mcap,application/json,application/octet-stream"
            multiple
            disabled={props.disabled}
            onChange={(event) =>
              props.onFilesChange(Array.from(event.target.files ?? []))
            }
          />
          <label
            htmlFor="browser-upload-package"
            className={styles.packagePickerLabel}
            aria-disabled={props.disabled}
          >
            <FolderOpen size={28} aria-hidden="true" />
            <strong>选择数据包文件</strong>
            <span>同时选择 rollout_manifest.json 与 RAW_MCAP</span>
          </label>
          <div className={styles.selectedFiles} aria-live="polite">
            <span>
              <FileJson2 size={13} aria-hidden="true" />{" "}
              {manifestName ?? "等待 Manifest"}
            </span>
            <span>
              <CloudUpload size={13} aria-hidden="true" />{" "}
              {rawName ?? "等待 RAW_MCAP"}
            </span>
          </div>
        </div>
      ) : (
        <div className={styles.objectReferenceFields}>
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
              props.onFilesChange(Array.from(event.target.files ?? []))
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

      <section
        className={styles.targetFacts}
        aria-labelledby="upload-target-heading"
      >
        <div className={styles.subsectionTitle}>
          <span className={styles.stepBadge} aria-hidden="true">
            2
          </span>
          <h3 id="upload-target-heading">目标信息</h3>
        </div>
        <dl>
          <div>
            <dt>
              <Target size={13} aria-hidden="true" /> 项目
            </dt>
            <dd>{props.projectId}</dd>
          </div>
          <div>
            <dt>
              <MapPin size={13} aria-hidden="true" /> 区域
            </dt>
            <dd>{props.regionCode}</dd>
          </div>
          <div>
            <dt>采集任务</dt>
            <dd>{props.preflight?.manifest.task_id ?? "由 Manifest 识别"}</dd>
          </div>
          <div>
            <dt>机器人</dt>
            <dd>
              {props.preflight?.identifiers.robot_id ?? "由 Manifest 识别"}
            </dd>
          </div>
        </dl>
        <Tag variant="filled" color="blue">
          相机与 Topic 上传后自动发现，只读
        </Tag>
      </section>
    </section>
  );
}
