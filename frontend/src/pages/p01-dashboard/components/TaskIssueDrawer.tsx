import { Alert, Button, Descriptions, Space, Spin, Table } from "antd";
import { lazy, Suspense, useState } from "react";
import type {
  DashboardDataIssue,
  DashboardScope,
  DashboardTaskListItem,
} from "../../../features/dashboard/types";
import { EntityDrawer } from "../../../shared/ui";
import { VideoPreviewModal } from "../../../features/viewer/VideoPreviewModal";

const OriginalSourceBrowser = lazy(
  () => import("../../p03-upload-jobs/components/OriginalSourceBrowser"),
);
const DuplicateIssueActions = lazy(() => import("./DuplicateIssueActions"));
const ResumeProcessingAction = lazy(() => import("./ResumeProcessingAction"));

const findingLabels: Readonly<Record<string, string>> = {
  QC_IMAGE_BLACK: "黑帧比例超标",
  QC_IMAGE_REPEATED: "重复帧比例超标",
};

function issueSummary(issue: DashboardDataIssue): string {
  const findings = issue.findings ?? [];
  return findings.length
    ? [
        ...new Set(
          findings.map((item) => findingLabels[item.code] ?? item.message),
        ),
      ].join("、")
    : issue.label;
}

function findingValue(
  code: string,
  value: number | string | null | undefined,
): string {
  if (value == null) return "—";
  if (typeof value === "number" && code in findingLabels) {
    return `${(value * 100).toFixed(2)}%`;
  }
  return String(value);
}

export default function TaskIssueDrawer({
  scope,
  issues,
  tasks,
  title,
  onClose,
}: Readonly<{
  scope?: DashboardScope;
  issues: readonly DashboardDataIssue[];
  tasks: readonly DashboardTaskListItem[];
  title: string;
  onClose: () => void;
}>) {
  const [selectedIssue, setSelectedIssue] = useState<DashboardDataIssue | null>(
    null,
  );
  const [previewOpen, setPreviewOpen] = useState(false);
  const selected =
    issues.find((item) => item.rollout_id === selectedIssue?.rollout_id) ??
    selectedIssue;
  const canPreview =
    scope &&
    selected?.source_import_id &&
    selected.source_episode_index != null;
  return (
    <EntityDrawer
      open
      title={`${title}（${issues.length}）`}
      onClose={onClose}
      width="min(960px, 95vw)"
    >
      {selected ? (
        <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <Button
            onClick={() => {
              setSelectedIssue(null);
              setPreviewOpen(false);
            }}
          >
            返回问题列表
          </Button>
          <Alert
            type="warning"
            title={selected.label}
            description={selected.description}
          />
          <Descriptions
            bordered
            size="small"
            column={1}
            items={[
              {
                key: "package",
                label: "数据包",
                children: selected.data_package_id,
              },
              {
                key: "episode",
                label: "Episode",
                children: selected.source_episode_index ?? "—",
              },
              {
                key: "qc",
                label: "质检状态",
                children:
                  (
                    { PASS: "通过", RISK: "风险", REJECT: "拒绝" } as Record<
                      string,
                      string
                    >
                  )[selected.qc_status ?? ""] ?? "未完成",
              },
              {
                key: "ready",
                label: "入库状态",
                children: selected.lance_ready ? "已入库" : "未入库",
              },
              {
                key: "code",
                label: "原因代码",
                children: selected.reason_code,
              },
              ...(selected.duplicate_of_rollout_id
                ? [
                    {
                      key: "original",
                      label: "原数据包",
                      children: selected.duplicate_of_rollout_id,
                    },
                  ]
                : []),
              ...(selected.alignment_attempt_id
                ? [
                    {
                      key: "attempt",
                      label: "历史处理记录",
                      children: selected.alignment_attempt_id,
                    },
                  ]
                : []),
            ]}
          />
          {(selected.findings?.length ?? 0) > 0 ? (
            <Table
              size="small"
              pagination={false}
              dataSource={selected.findings?.map((item, index) => ({
                ...item,
                key: index,
              }))}
              scroll={{ x: 650 }}
              columns={[
                {
                  title: "检测问题",
                  key: "finding",
                  render: (_, item) => findingLabels[item.code] ?? item.message,
                },
                {
                  title: "相机 / 通道",
                  dataIndex: "topic",
                  render: (value: string | null) => value ?? "—",
                },
                {
                  title: "检测值",
                  key: "observed",
                  render: (_, item) => findingValue(item.code, item.observed),
                },
                {
                  title: "阈值",
                  key: "threshold",
                  render: (_, item) => findingValue(item.code, item.threshold),
                },
                {
                  title: "检测区间（秒）",
                  key: "window",
                  render: (_, item) =>
                    item.start_ns != null && item.end_ns != null
                      ? `${(item.start_ns / 1e9).toFixed(2)}–${(item.end_ns / 1e9).toFixed(2)}`
                      : "—",
                },
              ]}
            />
          ) : selected.category === "QUALITY" ? (
            <Alert
              type="info"
              title="该记录暂无详细检测报告，可查看原始数据。"
            />
          ) : null}
          {scope &&
          selected.source_import_id &&
          selected.source_episode_index != null &&
          selected.reason_code === "ALIGNMENT_ATTEMPT_IMMUTABLE" &&
          selected.alignment_attempt_id ? (
            <Suspense fallback={<Spin description="正在加载处理操作" />}>
              <DuplicateIssueActions
                key={selected.rollout_id}
                scope={scope}
                issue={selected}
              />
            </Suspense>
          ) : null}
          {selected.category === "RESUME_REQUIRED" &&
          scope &&
          selected.source_import_id ? (
            <Suspense fallback={<Spin description="正在加载处理操作" />}>
              <ResumeProcessingAction
                key={selected.source_import_id}
                scope={scope}
                importId={selected.source_import_id}
              />
            </Suspense>
          ) : null}
          {canPreview ? (
            <Button type="primary" onClick={() => setPreviewOpen(true)}>
              查看原始数据与视频
            </Button>
          ) : (
            <p>此记录暂无可预览的原始数据来源。</p>
          )}
        </Space>
      ) : (
        <>
          <p>
            共 {issues.length}{" "}
            个问题数据包。点击“查看详情”可查看原因、检测值和对应原始数据。
          </p>
          <Table
            rowKey="rollout_id"
            size="small"
            dataSource={[...issues]}
            pagination={{
              pageSize: 10,
              showSizeChanger: false,
              hideOnSinglePage: true,
            }}
            scroll={{ x: 650 }}
            locale={{ emptyText: "当前没有匹配的问题数据，请刷新任务状态。" }}
            columns={[
              {
                title: "数据包 / Episode",
                key: "episode",
                render: (_, item) => (
                  <span title={item.data_package_id}>
                    {item.source_episode_index != null
                      ? `Episode ${item.source_episode_index}`
                      : item.data_package_id}
                  </span>
                ),
              },
              {
                title: "采集任务",
                key: "task",
                render: (_, item) =>
                  tasks.find((task) => task.taskId === item.task_id)?.name ??
                  item.task_id,
              },
              {
                title: "问题",
                key: "problem",
                render: (_, item) => issueSummary(item),
              },
              {
                title: "操作",
                key: "actions",
                render: (_, item) => (
                  <Button
                    type="link"
                    aria-label={`查看 ${item.source_episode_index != null ? `Episode ${item.source_episode_index}` : item.data_package_id} 详情`}
                    onClick={() => setSelectedIssue(item)}
                  >
                    查看详情
                  </Button>
                ),
              },
            ]}
          />
        </>
      )}
      {previewOpen && canPreview ? (
        <VideoPreviewModal
          open
          title="原始数据与视频"
          onCancel={() => setPreviewOpen(false)}
        >
          <Suspense fallback={<Spin description="正在加载原始数据" />}>
            <OriginalSourceBrowser
              key={`${selected.source_import_id}:${selected.source_episode_index}`}
              scope={scope}
              importId={selected.source_import_id!}
              initialEpisodeIndex={selected.source_episode_index!}
            />
          </Suspense>
        </VideoPreviewModal>
      ) : null}
    </EntityDrawer>
  );
}
