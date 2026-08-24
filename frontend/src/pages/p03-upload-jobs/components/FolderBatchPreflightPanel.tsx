import { Alert, Skeleton, Tag } from "antd";
import { Check, CircleAlert, FolderTree } from "lucide-react";
import type { FolderUploadDiscovery } from "../upload-contract";
import styles from "../styles.module.css";

export function FolderBatchPreflightPanel(props: {
  readonly status: "idle" | "scanning" | "ready" | "error";
  readonly discovery: FolderUploadDiscovery | null;
}) {
  const bundles = props.discovery?.bundles ?? [];
  const failures = props.discovery?.failures ?? [];

  return (
    <section
      className={`${styles.uploadPanel} ${styles.preflightPanel}`}
      aria-labelledby="folder-batch-preflight-heading"
    >
      <header className={styles.panelHeader}>
        <span className={styles.stepBadge} aria-hidden="true">
          3
        </span>
        <div>
          <h2 id="folder-batch-preflight-heading">
            目录数据包识别 <small>（按 Manifest 分组）</small>
          </h2>
        </div>
        {props.status === "ready" ? (
          <Tag icon={<Check size={12} />} color="success">
            {bundles.length} 个可上传数据包
          </Tag>
        ) : null}
      </header>

      {props.status === "scanning" ? (
        <div
          className={styles.preflightSkeleton}
          aria-label="正在识别目录数据包"
        >
          <Skeleton active paragraph={{ rows: 5 }} title={{ width: "48%" }} />
        </div>
      ) : props.status === "error" ? (
        <div className={styles.preflightError}>
          <Alert
            type="error"
            showIcon
            icon={<CircleAlert size={18} />}
            title="目录中没有可安全上传的数据包"
            description={
              failures.length > 0 ? (
                <ul className={styles.folderFailureList}>
                  {failures.slice(0, 8).map((failure) => (
                    <li key={`${failure.relativePath}:${failure.code}`}>
                      <code>{failure.relativePath}</code>：{failure.detail}
                    </li>
                  ))}
                  {failures.length > 8 ? (
                    <li>另有 {failures.length - 8} 项不能进入批量上传。</li>
                  ) : null}
                </ul>
              ) : (
                "每个数据包必须在同一目录树中包含一个 Manifest 及其声明的 RAW_MCAP。"
              )
            }
          />
        </div>
      ) : props.status !== "ready" ? (
        <div className={styles.preflightEmpty}>
          <FolderTree size={38} aria-hidden="true" />
          <strong>等待选择采集目录</strong>
          <span>支持任意层级目录；同名 recording.mcap 不会跨目录匹配。</span>
        </div>
      ) : (
        <div className={styles.preflightContent}>
          <ol className={styles.signalRail} aria-label="目录批量上传处理阶段">
            <li data-state="done">扫描目录</li>
            <li data-state="done">匹配 Manifest 与 RAW</li>
            <li data-state="current">逐包服务端预检与上传</li>
            <li>全部提交</li>
          </ol>
          <p className={styles.folderBatchSummary}>
            将按目录中的 Manifest
            逐条建立独立上传会话。单条失败只进入失败清单，其余数据包继续；
            网络恢复后会从服务端已确认分片续传。
          </p>
          {failures.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              icon={<CircleAlert size={17} />}
              title={`${failures.length} 个目录项不能进入批量上传`}
              description={
                <ul className={styles.folderFailureList}>
                  {failures.slice(0, 8).map((failure) => (
                    <li key={`${failure.relativePath}:${failure.code}`}>
                      <code>{failure.relativePath}</code>：{failure.detail}
                    </li>
                  ))}
                  {failures.length > 8 ? (
                    <li>
                      其余 {failures.length - 8} 项将在选择修正后重新扫描。
                    </li>
                  ) : null}
                </ul>
              }
            />
          ) : null}
          <div
            className={styles.folderBatchPaths}
            aria-label="可上传数据包目录"
          >
            {bundles.slice(0, 8).map((bundle) => (
              <span key={bundle.id}>
                <strong>{bundle.manifest.data_package_id}</strong>
                <small>{bundle.relativeDirectory || "所选目录根"}</small>
              </span>
            ))}
            {bundles.length > 8 ? (
              <small>另有 {bundles.length - 8} 个数据包</small>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
