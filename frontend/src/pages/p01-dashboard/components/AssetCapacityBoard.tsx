import { Select } from "antd";
import {
  Camera,
  CloudDownload,
  Database,
  Send,
  ShieldCheck,
  Tags,
  UserRoundCheck,
  type LucideIcon,
} from "lucide-react";
import { lazy, Suspense, useState } from "react";
import type {
  DashboardDataIssue,
  DashboardScope,
  DashboardTaskStatus,
} from "../../../features/dashboard/types";
import { StatusTag } from "../../../shared/ui";
import {
  DashboardSectionNotice,
  sectionLabel,
  sectionTone,
} from "./DashboardSectionNotice";
import styles from "./AssetCapacityBoard.module.css";

const TaskIssueDrawer = lazy(() => import("./TaskIssueDrawer"));

type TaskStage = DashboardTaskStatus["pipeline"]["stages"][number];
type TaskStageCode = TaskStage["stage"];

const ALL_TASKS_VALUE = "__all_tasks__";

const stageOrder = [
  "TASK_EXECUTION",
  "PACKAGE_UPLOAD",
  "RAW_RECEIPT",
  "AUTOMATIC_VALIDATION",
  "STANDARDIZATION",
  "ANNOTATION",
  "REVIEW",
  "PUBLICATION",
] as const satisfies readonly TaskStageCode[];

const stagePresentation: Readonly<
  Record<
    TaskStageCode,
    Readonly<{ label: string; eyebrow: string; Icon: LucideIcon }>
  >
> = {
  TASK_EXECUTION: { label: "采集", eyebrow: "任务执行", Icon: Camera },
  PACKAGE_UPLOAD: { label: "登记上传", eyebrow: "已上传", Icon: CloudDownload },
  RAW_RECEIPT: { label: "Raw 接收", eyebrow: "已接收", Icon: CloudDownload },
  AUTOMATIC_VALIDATION: {
    label: "自动校验",
    eyebrow: "自动质检",
    Icon: ShieldCheck,
  },
  STANDARDIZATION: {
    label: "标准化入库",
    eyebrow: "已入库",
    Icon: Database,
  },
  ANNOTATION: { label: "数据标注", eyebrow: "标注", Icon: Tags },
  REVIEW: { label: "人工审核", eyebrow: "审核", Icon: UserRoundCheck },
  PUBLICATION: { label: "发布", eyebrow: "已发布", Icon: Send },
};

const lifecycleLabels = {
  ACTIVE: "ACTIVE / 进行中",
  CLOSED: "CLOSED / 已关闭",
  CANCELLED: "CANCELLED / 已取消",
} as const;

const attainmentLabels = {
  NOT_CONFIGURED: "未配置目标",
  IN_PROGRESS: "目标进行中",
  ATTAINED: "已达标",
  EXCEEDED: "已超额达标",
  UNKNOWN: "目标进度未知",
} as const;

const countFormatter = new Intl.NumberFormat("zh-CN");

const issueLabels: Record<DashboardDataIssue["category"], string> = {
  QUALITY: "质检问题",
  DUPLICATE: "重复数据",
  TECHNICAL: "处理失败",
  PROCESSING_CONFLICT: "处理冲突",
  RESUME_REQUIRED: "待继续处理",
};

function stageMeta(
  stage: TaskStage,
  currentStage?: TaskStageCode,
): string | null {
  const parts: string[] = [];
  if (currentStage && stage.stage === currentStage) parts.push("当前");
  if (stage.waiting > 0) {
    const waitingLabel =
      stage.stage === "AUTOMATIC_VALIDATION" ? "未质检" : "等待";
    parts.push(`${waitingLabel} ${countFormatter.format(stage.waiting)}`);
  }
  if (stage.running > 0)
    parts.push(`运行 ${countFormatter.format(stage.running)}`);
  if (stage.blocked > 0 && stage.stage !== "STANDARDIZATION")
    parts.push(`阻塞 ${countFormatter.format(stage.blocked)}`);
  if (stage.unavailable > 0)
    parts.push(`不可用 ${countFormatter.format(stage.unavailable)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function AssetCapacityBoard({
  taskStatus,
  onTaskChange,
  scope,
}: Readonly<{
  scope?: DashboardScope;
  taskStatus: DashboardTaskStatus;
  onTaskChange: (taskId: string | null) => void;
}>) {
  const selected = taskStatus.selected;
  const pipeline = taskStatus.pipeline;
  const stagesByCode = new Map(
    pipeline.stages.map((stage) => [stage.stage, stage]),
  );
  const [issueFilter, setIssueFilter] = useState<{
    stage: TaskStageCode;
    category: DashboardDataIssue["category"];
  } | null>(null);
  const issues = pipeline.issues ?? [];

  return (
    <section
      className={styles.panel}
      aria-labelledby="signal-pipeline-title"
      data-section-status={taskStatus.section.status}
    >
      <header className={styles.header}>
        <div className={styles.heading}>
          <div className={styles.titleLine}>
            <h2 id="signal-pipeline-title">信号轨道</h2>
            <StatusTag
              status={taskStatus.section.status}
              label={sectionLabel(taskStatus.section.status)}
              known
              tone={sectionTone(taskStatus.section.status)}
            />
          </div>
        </div>

        <div className={styles.taskContext}>
          {taskStatus.tasks.length > 0 ? (
            <label className={styles.taskSelector}>
              <span>任务范围</span>
              <Select
                aria-label="筛选采集任务"
                value={taskStatus.selectedTaskId ?? ALL_TASKS_VALUE}
                options={[
                  {
                    value: ALL_TASKS_VALUE,
                    label: `全部任务（${countFormatter.format(taskStatus.tasks.length)}）`,
                  },
                  ...taskStatus.tasks.map((task) => ({
                    value: task.taskId,
                    label: `${task.taskCode} · ${task.name}`,
                  })),
                ]}
                onChange={(value) =>
                  onTaskChange(value === ALL_TASKS_VALUE ? null : value)
                }
              />
            </label>
          ) : null}
          {selected ? (
            <div className={styles.taskMeta}>
              <div className={styles.taskCopy}>
                <span>当前任务 · {selected.task.taskCode}</span>
                <strong title={selected.task.name}>{selected.task.name}</strong>
              </div>
              <div className={styles.statusLine}>
                <StatusTag
                  status={selected.task.lifecycle}
                  label={lifecycleLabels[selected.task.lifecycle]}
                  known
                  tone={
                    selected.task.lifecycle === "ACTIVE" ? "info" : "neutral"
                  }
                />
                <StatusTag
                  status={selected.attainment}
                  label={attainmentLabels[selected.attainment]}
                  known={selected.attainment !== "UNKNOWN"}
                  tone={
                    selected.attainment === "ATTAINED" ||
                    selected.attainment === "EXCEEDED"
                      ? "success"
                      : "neutral"
                  }
                />
              </div>
            </div>
          ) : taskStatus.tasks.length > 0 ? (
            <div className={styles.taskMeta} aria-label="全部任务汇总">
              <div className={styles.taskCopy}>
                <span>当前范围 · 全部任务</span>
                <strong>
                  {countFormatter.format(pipeline.taskCount)} 个任务 ·{" "}
                  {countFormatter.format(pipeline.packageCount)} 个数据包
                </strong>
              </div>
            </div>
          ) : null}
        </div>
      </header>

      <ol className={styles.rail} aria-label="采集到发布的固定八阶段">
        {stageOrder.map((stageCode) => {
          const stage = stagesByCode.get(stageCode);
          const { label, eyebrow, Icon } = stagePresentation[stageCode];
          const meta = stage ? stageMeta(stage, selected?.currentStage) : null;
          const tone = !stage
            ? undefined
            : stage.blocked + stage.failed + stage.unavailable > 0
              ? "danger"
              : stage.stage === "AUTOMATIC_VALIDATION" && stage.isolated > 0
                ? "quality"
                : undefined;
          return (
            <li
              key={stageCode}
              className={styles.stage}
              data-stage={stageCode}
              data-current={
                selected?.currentStage === stageCode ? "true" : undefined
              }
            >
              <span className={styles.stageLabel}>{label}</span>
              <span className={styles.stageIcon} aria-hidden="true">
                <Icon size={20} strokeWidth={1.8} />
              </span>
              <strong>{eyebrow}</strong>
              <span className={styles.stageFact}>
                {countFormatter.format(stage?.succeeded ?? 0)} 个数据包
              </span>
              {meta ? (
                <span className={styles.stageMeta} data-tone={tone}>
                  {meta}
                </span>
              ) : null}
              {(
                [
                  {
                    category: "QUALITY",
                    label: "质检问题",
                    count:
                      stageCode === "AUTOMATIC_VALIDATION"
                        ? pipeline.qc.risk + pipeline.qc.rejected
                        : 0,
                  },
                  {
                    category: "DUPLICATE",
                    label: "重复数据",
                    count:
                      stageCode === "AUTOMATIC_VALIDATION"
                        ? (pipeline.qc.duplicate ?? 0)
                        : 0,
                  },
                  {
                    category: "PROCESSING_CONFLICT",
                    label: "处理冲突",
                    count:
                      stageCode === "STANDARDIZATION"
                        ? (pipeline.qc.reprocessingConflicts ?? 0)
                        : 0,
                  },
                  {
                    category: "RESUME_REQUIRED",
                    label: "待继续处理",
                    count: issues.filter(
                      (issue) =>
                        issue.stage === stageCode &&
                        issue.category === "RESUME_REQUIRED",
                    ).length,
                  },
                  {
                    category: "TECHNICAL",
                    label: "处理失败",
                    count: stage?.failed ?? 0,
                  },
                ] as const
              ).map(({ category, label: issueLabel, count }) =>
                count > 0 ? (
                  <button
                    type="button"
                    key={category}
                    className={styles.issueButton}
                    data-tone={category === "TECHNICAL" ? "danger" : "quality"}
                    onClick={() =>
                      setIssueFilter({ stage: stageCode, category })
                    }
                  >
                    {issueLabel} {countFormatter.format(count)}
                  </button>
                ) : null,
              )}
              {stageCode === "STANDARDIZATION" &&
              (pipeline.qc.discarded ?? 0) > 0 ? (
                <span>
                  已移除 {countFormatter.format(pipeline.qc.discarded!)}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>

      {issueFilter ? (
        <Suspense fallback={<span role="status">正在加载问题数据…</span>}>
          <TaskIssueDrawer
            key={`${issueFilter.stage}:${issueFilter.category}`}
            scope={scope}
            issues={issues.filter(
              (issue) =>
                issue.stage === issueFilter.stage &&
                issue.category === issueFilter.category,
            )}
            tasks={taskStatus.tasks}
            title={`${stagePresentation[issueFilter.stage].label} · ${issueLabels[issueFilter.category]}`}
            onClose={() => setIssueFilter(null)}
          />
        </Suspense>
      ) : null}

      {taskStatus.tasks.length === 0 ? (
        <div className={styles.chooseNotice} role="status">
          <strong>当前范围没有采集任务</strong>
          <span>创建并关联任务后，信号轨道将自动显示全部任务的真实流转。</span>
        </div>
      ) : null}

      <DashboardSectionNotice
        section={taskStatus.section}
        label="信号轨道"
        compact
      />
    </section>
  );
}
