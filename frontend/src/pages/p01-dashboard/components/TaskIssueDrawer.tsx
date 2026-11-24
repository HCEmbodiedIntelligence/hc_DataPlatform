import { Alert, Button, Descriptions, Space, Spin, Table } from "antd";
import { lazy, Suspense, useState } from "react";
import { captureProblemLabels } from "../../../features/capture-schemes/presentation";
import { findingLabel, findingMeasurement } from "../../../features/quality/RecordingQualityReview";
import { RecordingProcessingDetails } from "../../../features/quality/RecordingProcessingDetails";
import type {
  DashboardDataIssue,
  DashboardScope,
  DashboardTaskListItem,
} from "../../../features/dashboard/types";
import { useCapabilities } from "../../../shared/auth/use-capabilities";
import { DiscardRecordingsModal, type DiscardTarget } from "./DiscardRecordingsModal";
import { DashboardMcapRetry } from "./DashboardMcapRetry";
import { EntityDrawer } from "../../../shared/ui";
import { VideoPreviewModal } from "../../../features/viewer/VideoPreviewModal";

const OriginalSourceBrowser = lazy(
  () => import("../../../features/ingest/browser/OriginalSourceBrowser"),
);
const DuplicateIssueActions = lazy(() => import("./DuplicateIssueActions"));
const ResumeProcessingAction = lazy(() => import("./ResumeProcessingAction"));

function rowKey(issue: DashboardDataIssue) {
  return JSON.stringify([issue.task_id, issue.rollout_id, issue.recording_id]);
}

function issueSummary(issue: DashboardDataIssue): string {
  const findings = issue.findings ?? [];
  return findings.length
    ? [
        ...new Set(
          findings.map((item) => findingLabel(item.code, item.message)),
        ),
      ].join("、")
    : issue.label;
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
  const canManage = useCapabilities().has("upload.manage");
  const [checked, setChecked] = useState<string[]>([]);
  const [discardTargets, setDiscardTargets] = useState<DiscardTarget[] | null>(null);
  const candidates = issues.filter(issue => issue.category === "QUALITY" && issue.recording_id);
  const enableDiscard = !!scope && canManage && candidates.length > 0;
  const checkedKeys = new Set(checked);
  const chosen = candidates.filter(issue => checkedKeys.has(rowKey(issue)));
  const targets = [...new Map(chosen.map(issue => [issue.recording_id!, {
    id: issue.recording_id!, label: issue.data_package_id,
  }])).values()];
  const [previewOpen, setPreviewOpen] = useState(false);
  const selected = issues.find(
    (item) =>
      item.task_id === selectedIssue?.task_id &&
      item.rollout_id === selectedIssue?.rollout_id,
  );
  const canPreview =
    scope &&
    selected?.source_import_id &&
    selected.source_episode_index != null;
  const commitIssue = selected?.reason_code === "EPISODE_PACKAGE_COMMIT_FAILED" && !!selected.recording_id;
  const rawProcessing = selected && scope && selected.source_import_id &&
    (selected.category === "RESUME_REQUIRED" || selected.category === "TECHNICAL" ||
      selected.reason_code === "RAW_PREPARATION_FAILED") ? (
      <Suspense fallback={<Spin description="正在加载处理操作" />}>
        <ResumeProcessingAction
          key={selected.source_import_id}
          scope={scope}
          importId={selected.source_import_id}
          displayedReasonCode={selected.reason_code}
          displayedPackageId={selected.data_package_id}
        />
      </Suspense>
    ) : null;
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
            description={
              <div style={{ whiteSpace: "pre-line", overflowWrap: "anywhere", lineHeight: 1.7 }}>
                {selected.description}
              </div>
            }
          />
          {selected.category === "QUALITY" && selected.recording_id && scope && (
            <RecordingProcessingDetails key={selected.recording_id} scope={scope} recordingId={selected.recording_id} />
          )}
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
                children: selected.reason_code
                  ? `${captureProblemLabels[selected.reason_code] ?? selected.label}（${selected.reason_code}）`
                  : "—",
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
                      label: "处理记录",
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
                  render: (_, item) => findingLabel(item.code, item.message),
                },
                {
                  title: "相机 / 通道",
                  dataIndex: "topic",
                  render: (value: string | null) => value ?? "—",
                },
                {
                  title: "检测值",
                  key: "observed",
                  render: (_, item) => findingMeasurement(item.code, item.observed),
                },
                {
                  title: "阈值",
                  key: "threshold",
                  render: (_, item) => findingMeasurement(item.code, item.threshold),
                },
                {
                  title: "报告检测区间（秒）",
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
          {!commitIssue && rawProcessing}
          {selected.recording_id && scope && selected.category === "QUALITY" ? null : selected.recording_id && scope ? (
            <RecordingProcessingDetails
              scope={scope}
              recordingId={selected.recording_id}
            />
          ) : canPreview ? (
            <Button type="primary" onClick={() => setPreviewOpen(true)}>
              查看原始数据与视频
            </Button>
          ) : (
            <p>此记录暂无可预览的原始数据来源。</p>
          )}
          {commitIssue && rawProcessing && (
            <details>
              <summary>改用其他方案重新准备录制</summary>
              {rawProcessing}
            </details>
          )}
        </Space>
      ) : (
        <>
          <p>
            共 {issues.length}{" "}
            个问题数据包。点击“查看详情”可查看原因、检测值和对应原始数据。
          </p>
          <Space wrap>
            <DashboardMcapRetry scope={scope} tasks={tasks} issues={issues} />
            {enableDiscard && <Button danger disabled={!targets.length} onClick={() => setDiscardTargets(targets)}>
              批量丢弃当前录制{targets.length > 0 && `（${targets.length}）`}
            </Button>}
          </Space>
          <Table
            rowKey={rowKey}
            rowSelection={enableDiscard ? {
              selectedRowKeys: chosen.map(rowKey),
              onChange: keys => setChecked(keys.map(String)),
              getCheckboxProps: issue => ({ disabled: issue.category !== "QUALITY" || !issue.recording_id }),
              selections: [
                { key: "all", text: "全选所有页的录制", onSelect: () => setChecked(candidates.map(rowKey)) },
                { key: "clear", text: "清空选择", onSelect: () => setChecked([]) },
              ],
            } : undefined}
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
      {discardTargets && scope && canManage && <DiscardRecordingsModal
        scope={scope} targets={discardTargets} onClose={() => setDiscardTargets(null)}
        onDiscarded={ids => {
          const succeeded = new Set(ids);
          const finished = new Set(chosen.filter(issue => succeeded.has(issue.recording_id!)).map(rowKey));
          setChecked(previous => previous.filter(key => !finished.has(key)));
        }}
      />}
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
