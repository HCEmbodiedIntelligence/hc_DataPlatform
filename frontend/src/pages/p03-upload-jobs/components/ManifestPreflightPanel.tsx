import { Alert, Button, Skeleton, Tag } from "antd";
import {
  Check,
  CircleAlert,
  FileCheck2,
  Fingerprint,
  ScanSearch,
} from "lucide-react";
import type { ManifestPreflight } from "../formal-client";
import {
  formatBytes,
  formatDuration,
  planUploadParts,
  type UploadProblemCopy,
} from "../upload-contract";
import styles from "../styles.module.css";

function compactFingerprint(value: string): string {
  return value.length > 18 ? `${value.slice(0, 9)}…${value.slice(-7)}` : value;
}

export function ManifestPreflightPanel(props: {
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly preflight: ManifestPreflight | null;
  readonly problem: UploadProblemCopy | null;
  readonly onRetry: () => void;
}) {
  const result = props.preflight;
  const rawFile = result?.files.find((file) => file.role === "RAW_MCAP");
  const plan = rawFile ? planUploadParts(rawFile.size) : null;
  const duration = result
    ? Math.max(
        0,
        (Date.parse(result.time_range.end_time) -
          Date.parse(result.time_range.start_time)) /
          1000,
      )
    : 0;

  return (
    <section
      className={`${styles.uploadPanel} ${styles.preflightPanel}`}
      aria-labelledby="manifest-preflight-heading"
    >
      <header className={styles.panelHeader}>
        <span className={styles.stepBadge} aria-hidden="true">
          3
        </span>
        <div>
          <h2 id="manifest-preflight-heading">
            Manifest 预检 <small>（自动解析）</small>
          </h2>
        </div>
        {props.status === "ready" ? (
          <Tag icon={<Check size={12} />} color="success">
            预检通过
          </Tag>
        ) : null}
      </header>

      {props.status === "loading" ? (
        <div
          className={styles.preflightSkeleton}
          aria-label="Manifest 正在预检"
        >
          <Skeleton active paragraph={{ rows: 8 }} title={{ width: "42%" }} />
        </div>
      ) : props.status === "error" && props.problem ? (
        <div className={styles.preflightError}>
          <Alert
            type="error"
            showIcon
            icon={<CircleAlert size={18} />}
            title={props.problem.title}
            description={
              <div>
                <p>{props.problem.detail}</p>
                {props.problem.problemCode ? (
                  <code>{props.problem.problemCode}</code>
                ) : null}
                {props.problem.requestId ? (
                  <p>
                    请求 ID：<code>{props.problem.requestId}</code>
                  </p>
                ) : null}
              </div>
            }
            action={
              props.problem.retryable ? (
                <Button size="small" onClick={props.onRetry}>
                  重新预检
                </Button>
              ) : undefined
            }
          />
          <div className={styles.preflightEmptyGuide}>
            <FileCheck2 size={32} aria-hidden="true" />
            <strong>修正 Manifest 后重新选择数据包</strong>
            <span>
              空文件、未知字段、路径穿越、重复键、过深 JSON 和超限包都会被拒绝。
            </span>
          </div>
        </div>
      ) : !result ? (
        <div className={styles.preflightEmpty}>
          <ScanSearch size={38} aria-hidden="true" />
          <strong>等待 Manifest</strong>
          <span>
            选择浏览器数据包或对象地址对应的 Manifest
            后，这里会展示只读预检事实。
          </span>
          <ol className={styles.signalRail} aria-label="上传处理阶段">
            <li data-state="current">读取声明</li>
            <li>预检身份</li>
            <li>规划分片</li>
            <li>提交 Raw</li>
          </ol>
        </div>
      ) : (
        <div className={styles.preflightContent}>
          <ol className={styles.signalRail} aria-label="上传处理阶段">
            <li data-state="done">读取声明</li>
            <li data-state="done">预检身份</li>
            <li data-state="current">规划 {plan?.partCount ?? 0} 个分片</li>
            <li>提交 Raw</li>
          </ol>

          <div className={styles.preflightFacts}>
            <section aria-labelledby="manifest-identifiers-heading">
              <h3 id="manifest-identifiers-heading">标识关系</h3>
              <dl>
                <div>
                  <dt>采集会话</dt>
                  <dd>{result.identifiers.collection_session_id}</dd>
                </div>
                <div>
                  <dt>录制请求</dt>
                  <dd>{result.identifiers.recording_request_id}</dd>
                </div>
                <div>
                  <dt>数据包</dt>
                  <dd>{result.identifiers.data_package_id}</dd>
                </div>
                <div>
                  <dt>机器人</dt>
                  <dd>{result.identifiers.robot_id}</dd>
                </div>
                <div>
                  <dt>时长</dt>
                  <dd>{formatDuration(duration)}</dd>
                </div>
                <div>
                  <dt>文件声明</dt>
                  <dd>
                    {result.files.length} 项 /{" "}
                    {formatBytes(result.total_file_size)}
                  </dd>
                </div>
              </dl>
            </section>
            <section aria-labelledby="manifest-integrity-heading">
              <h3 id="manifest-integrity-heading">校验信息</h3>
              <dl>
                <div>
                  <dt>Manifest 指纹</dt>
                  <dd>
                    <Fingerprint size={12} aria-hidden="true" />{" "}
                    <code title={result.manifest_fingerprint}>
                      {compactFingerprint(result.manifest_fingerprint)}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>RAW SHA-256</dt>
                  <dd>
                    <code title={result.manifest.sha256}>
                      {compactFingerprint(result.manifest.sha256)}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>RAW CRC64</dt>
                  <dd>
                    <code>{result.manifest.crc64}</code>
                  </dd>
                </div>
                <div>
                  <dt>预计分片</dt>
                  <dd>
                    {plan
                      ? `${plan.partCount} × ${formatBytes(plan.partSize)}`
                      : "—"}
                  </dd>
                </div>
                <div>
                  <dt>压缩</dt>
                  <dd>{result.manifest.compression}</dd>
                </div>
                <div>
                  <dt>Schema</dt>
                  <dd>{result.schema_version}</dd>
                </div>
              </dl>
            </section>
          </div>

          <section
            className={styles.discoverySection}
            aria-labelledby="manifest-discovery-heading"
          >
            <div className={styles.sectionHeadingRow}>
              <h3 id="manifest-discovery-heading">自动发现的相机 / Topic</h3>
              <Tag variant="filled">来源：Manifest · 只读</Tag>
            </div>
            <div className={styles.discoveryGroups}>
              <div>
                <span>相机 {result.discovery.cameras.length}</span>
                <div>
                  {result.discovery.cameras.length ? (
                    result.discovery.cameras.map((camera) => (
                      <Tag key={camera.camera_id}>{camera.camera_id}</Tag>
                    ))
                  ) : (
                    <em>未发现相机</em>
                  )}
                </div>
              </div>
              <div>
                <span>Topic {result.discovery.topics.length}</span>
                <div>
                  {result.discovery.topics.length ? (
                    result.discovery.topics.map((topic) => (
                      <Tag
                        key={topic.name}
                        color={topic.required ? "blue" : undefined}
                      >
                        {topic.name}
                      </Tag>
                    ))
                  ) : (
                    <em>未发现 Topic</em>
                  )}
                </div>
              </div>
            </div>
            {result.discovery.missing_expected_topics.length > 0 ? (
              <Alert
                className={styles.missingTopics}
                type="warning"
                showIcon
                title={`缺少 ${result.discovery.missing_expected_topics.length} 个预期 Topic`}
                description={result.discovery.missing_expected_topics.join(
                  "、",
                )}
              />
            ) : null}
          </section>

          <section
            className={styles.fileComposition}
            aria-labelledby="manifest-files-heading"
          >
            <div className={styles.sectionHeadingRow}>
              <h3 id="manifest-files-heading">文件组成</h3>
              <span>最多 256 条声明</span>
            </div>
            <div className={styles.fileTableScroller}>
              <table>
                <thead>
                  <tr>
                    <th>文件路径</th>
                    <th>角色 / 类型</th>
                    <th>大小</th>
                    <th>校验声明</th>
                  </tr>
                </thead>
                <tbody>
                  {result.files.map((file) => (
                    <tr key={file.path}>
                      <td title={file.path}>{file.path}</td>
                      <td>
                        {file.role} · {file.media_type}
                      </td>
                      <td>{formatBytes(file.size)}</td>
                      <td>
                        <code>{compactFingerprint(file.sha256)}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
