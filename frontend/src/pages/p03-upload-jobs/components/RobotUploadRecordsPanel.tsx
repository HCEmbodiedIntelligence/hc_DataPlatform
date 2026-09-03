import { Alert, Button, Empty, Spin, Table, Tag, Typography } from "antd";
import type { IngestScope } from "../../../entities/data-source";
import { useRobotUploadEpisodes } from "../../../features/robot-ingest/api";
import type { RobotUploadSummary } from "../../../features/robot-ingest/model";
import { formatStorageSize } from "../../../shared/lib/metric-presentation";
import styles from "../styles.module.css";

function date(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("zh-CN");
}

function EpisodeLineage({
  scope,
  upload,
}: {
  readonly scope: IngestScope | null;
  readonly upload: RobotUploadSummary;
}) {
  const lineage = useRobotUploadEpisodes(
    scope,
    upload.upload_id,
    upload.state === "COMMITTED" && upload.raw_source_id !== null,
  );
  if (lineage.isPending) return <Spin size="small" />;
  if (lineage.isError) {
    return <Alert type="error" showIcon title="Episode / QC 明细加载失败" />;
  }
  if (!lineage.data || lineage.data.items.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="尚未生成 Episode"
      />
    );
  }
  return (
    <Table
      size="small"
      rowKey="episode_id"
      pagination={false}
      dataSource={[...lineage.data.items]}
      columns={[
        {
          title: "Episode",
          dataIndex: "episode_id",
          render: (value: string) => <code>{value}</code>,
        },
        { title: "处理状态", dataIndex: "status", width: 120 },
        {
          title: "帧 / 样本",
          width: 150,
          render: (_, item) =>
            `${item.frame_count ?? "—"} / ${item.sample_count ?? "—"}`,
        },
        {
          title: "Dataset / Lance",
          width: 170,
          render: (_, item) =>
            `v${item.dataset_version ?? "—"} / v${item.lance_version ?? "—"}`,
        },
        {
          title: "QC 结果",
          width: 220,
          render: (_, item) => (
            <span className={styles.robotRecordIdentity}>
              <Tag>{item.quality_status}</Tag>
              <code>{item.qc_report_id ?? "尚无报告"}</code>
            </span>
          ),
        },
      ]}
    />
  );
}

export function RobotUploadRecordsPanel({
  scope,
  robotId,
  items,
  loading,
  errorMessage,
  onClear,
  onRefresh,
}: {
  readonly scope: IngestScope | null;
  readonly robotId: string;
  readonly items: readonly RobotUploadSummary[];
  readonly loading: boolean;
  readonly errorMessage: string | null;
  readonly onClear: () => void;
  readonly onRefresh: () => void;
}) {
  return (
    <section
      className={styles.robotRecordsPanel}
      aria-labelledby="robot-records-title"
    >
      <header>
        <div>
          <span>ROBOT RAW INGEST</span>
          <h2 id="robot-records-title">机器人统一上传记录</h2>
          <p>
            <code>{robotId}</code> · 当前项目使用历史；归属来自采集任务解析。
          </p>
        </div>
        <div>
          <Button onClick={onRefresh}>刷新</Button>
          <Button type="link" onClick={onClear}>
            返回全部上传
          </Button>
        </div>
      </header>
      {errorMessage ? (
        <Alert type="error" showIcon title={errorMessage} />
      ) : null}
      <Table<RobotUploadSummary>
        className="hc-data-table"
        rowKey="upload_id"
        loading={loading}
        dataSource={[...items]}
        pagination={false}
        locale={{ emptyText: "该机器人在当前项目暂无统一上传记录" }}
        scroll={{ x: 1020 }}
        expandable={{
          rowExpandable: (upload) =>
            upload.state === "COMMITTED" && upload.raw_source_id !== null,
          expandedRowRender: (upload) => (
            <EpisodeLineage scope={scope} upload={upload} />
          ),
        }}
        columns={[
          {
            title: "任务 / Raw",
            key: "identity",
            width: 250,
            render: (_, upload) => (
              <span className={styles.robotRecordIdentity}>
                <code>{upload.target.collection_task_id}</code>
                <Typography.Text type="secondary">
                  {upload.raw_source_id ?? upload.upload_id}
                </Typography.Text>
              </span>
            ),
          },
          {
            title: "格式",
            key: "format",
            width: 160,
            render: (_, upload) => (
              <span>
                {upload.source_format}{" "}
                <small>v{upload.source_format_version}</small>
              </span>
            ),
          },
          {
            title: "采集模式",
            dataIndex: "capture_mode",
            width: 130,
          },
          {
            title: "Raw 数据量",
            dataIndex: "total_bytes",
            width: 120,
            render: (value: number) => formatStorageSize(value),
          },
          {
            title: "Episode",
            key: "episodes",
            width: 180,
            render: (_, upload) =>
              `声明 ${upload.declared_episode_count ?? "—"} · 验证 ${upload.verified_episode_count ?? "—"} · 派生 ${upload.derived_episode_count}`,
          },
          {
            title: "帧 / 样本",
            width: 140,
            render: (_, upload) =>
              `${upload.verified_frame_count} / ${upload.verified_sample_count}`,
          },
          {
            title: "状态",
            key: "status",
            width: 140,
            render: (_, upload) => (
              <span className={styles.robotRecordStatus}>
                <Tag color={upload.state === "COMMITTED" ? "green" : "blue"}>
                  {upload.state}
                </Tag>
                <small>{upload.processing_status}</small>
                <small>{upload.quality_status}</small>
                <small>
                  QC {upload.qc_pass_episode_count} /{" "}
                  {upload.qc_risk_episode_count} /{" "}
                  {upload.qc_reject_episode_count}
                </small>
              </span>
            ),
          },
          {
            title: "创建时间",
            dataIndex: "created_at",
            width: 180,
            render: (value: string) => date(value),
          },
        ]}
      />
    </section>
  );
}
