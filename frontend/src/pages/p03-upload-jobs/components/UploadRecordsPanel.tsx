import { Alert, Button, Empty, Input, Select, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { RefreshCw, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { routes } from "../../../features/ingest/routing";
import type { FormalUploadSession, UploadStatus } from "../formal-client";
import { formatBytes, type UploadProblemCopy } from "../upload-contract";
import styles from "../styles.module.css";

const statusLabels: Readonly<Record<UploadStatus, string>> = {
  REGISTERED: "已登记",
  UPLOADING: "上传中",
  PAUSED: "传输暂停",
  MULTIPART_COMPLETED: "分片已完成",
  RAW_COMMITTED: "Raw 已提交",
  FAILED: "失败",
  CANCELLED: "已取消",
};

function statusColor(status: UploadStatus) {
  if (status === "RAW_COMMITTED") return "success";
  if (status === "FAILED" || status === "CANCELLED") return "error";
  if (status === "PAUSED") return "warning";
  return "processing";
}

export function UploadRecordsPanel(props: {
  readonly items: readonly FormalUploadSession[];
  readonly total: number;
  readonly loading: boolean;
  readonly problem: UploadProblemCopy | null;
  readonly packageFilter: string;
  readonly statusFilter: UploadStatus | undefined;
  readonly onPackageFilterChange: (value: string) => void;
  readonly onStatusFilterChange: (value: UploadStatus | undefined) => void;
  readonly onRefresh: () => void;
}) {
  const columns: ColumnsType<FormalUploadSession> = [
    {
      title: "数据包 / 会话",
      key: "identity",
      width: 250,
      render: (_, item) => (
        <div className={styles.recordIdentity}>
          <strong>{item.data_package_id}</strong>
          <code>{item.session_id ?? "—"}</code>
        </div>
      ),
    },
    {
      title: "来源",
      dataIndex: "source_type",
      key: "source",
      width: 150,
      render: (value: FormalUploadSession["source_type"]) =>
        value === "BROWSER_MULTIPART" ? "浏览器分片" : "授权对象地址",
    },
    {
      title: "大小",
      dataIndex: "expected_size",
      key: "size",
      width: 110,
      render: (value: number) => formatBytes(value),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (value: UploadStatus) => (
        <Tag color={statusColor(value)}>{statusLabels[value]}</Tag>
      ),
    },
    {
      title: "失败原因",
      dataIndex: "failure_code",
      key: "failure",
      width: 180,
      render: (value: string | null | undefined) =>
        value ? <code>{value}</code> : "—",
    },
    {
      title: "最近更新",
      dataIndex: "updated_at",
      key: "updated",
      width: 180,
      render: (value: string | undefined) =>
        value ? (
          <time dateTime={value}>
            {new Intl.DateTimeFormat("zh-CN", {
              dateStyle: "short",
              timeStyle: "medium",
            }).format(new Date(value))}
          </time>
        ) : (
          "—"
        ),
    },
    {
      title: "操作",
      key: "action",
      width: 100,
      fixed: "right",
      render: (_, item) =>
        item.session_id ? (
          <Link to={routes.uploads.build({ uploadId: item.session_id })}>
            查看详情
          </Link>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <section
      className={styles.recordsPanel}
      aria-labelledby="upload-records-heading"
    >
      <header>
        <div>
          <h2 id="upload-records-heading">上传记录</h2>
          <p>展示当前项目与区域的服务端持久化事实，共 {props.total} 条。</p>
        </div>
        <Button
          icon={<RefreshCw size={14} />}
          loading={props.loading}
          onClick={props.onRefresh}
        >
          刷新
        </Button>
      </header>
      <div className={styles.recordsFilters}>
        <label>
          数据包 ID
          <Input
            allowClear
            value={props.packageFilter}
            prefix={<Search size={14} />}
            placeholder="精确查询 data_package_id"
            onChange={(event) =>
              props.onPackageFilterChange(event.target.value)
            }
          />
        </label>
        <label>
          上传状态
          <Select
            allowClear
            value={props.statusFilter}
            placeholder="全部状态"
            options={(Object.keys(statusLabels) as UploadStatus[]).map(
              (value) => ({ value, label: statusLabels[value] }),
            )}
            onChange={props.onStatusFilterChange}
          />
        </label>
      </div>
      {props.problem ? (
        <Alert
          type="error"
          showIcon
          title={props.problem.title}
          description={
            <span>
              {props.problem.detail}
              {props.problem.requestId
                ? ` 请求 ID：${props.problem.requestId}`
                : ""}
            </span>
          }
          action={
            props.problem.retryable ? (
              <Button size="small" onClick={props.onRefresh}>
                重试
              </Button>
            ) : undefined
          }
        />
      ) : (
        <Table
          className={styles.recordsTable}
          rowKey={(item) =>
            item.session_id ??
            `${item.data_package_id}-${item.created_at ?? ""}`
          }
          columns={columns}
          dataSource={[...props.items]}
          loading={props.loading}
          pagination={false}
          scroll={{ x: 1090 }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  props.packageFilter || props.statusFilter
                    ? "当前筛选没有上传记录"
                    : "还没有上传记录"
                }
              />
            ),
          }}
          size="small"
        />
      )}
    </section>
  );
}
