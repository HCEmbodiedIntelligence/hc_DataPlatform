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
import { Link } from "react-router-dom";
import type { DashboardTaskStatus } from "../../../features/dashboard/types";
import { StatusTag } from "../../../shared/ui";
import {
  DashboardSectionNotice,
  sectionLabel,
  sectionTone,
} from "./DashboardSectionNotice";
import styles from "./AssetCapacityBoard.module.css";

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

function stageMeta(
  stage: TaskStage,
  currentStage?: TaskStageCode,
  qc?: DashboardTaskStatus["pipeline"]["qc"],
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
  if (stage.risk > 0) parts.push(`风险 ${countFormatter.format(stage.risk)}`);
  if (stage.isolated > 0) {
    if (stage.stage === "AUTOMATIC_VALIDATION" && qc) {
      const qualityFindingCount = qc.risk + qc.rejected;
      const duplicateCount = qc.duplicate ?? 0;
      if (qualityFindingCount > 0) {
        parts.push(`质检问题 ${countFormatter.format(qualityFindingCount)}`);
      }
      if (duplicateCount > 0) {
        parts.push(`重复数据 ${countFormatter.format(duplicateCount)}`);
      }
    } else {
      parts.push(`上游问题隔离 ${countFormatter.format(stage.isolated)}`);
    }
  }
  if (stage.blocked > 0)
    parts.push(`阻塞 ${countFormatter.format(stage.blocked)}`);
  if (stage.failed > 0)
    parts.push(`失败 ${countFormatter.format(stage.failed)}`);
  if (stage.unavailable > 0)
    parts.push(`不可用 ${countFormatter.format(stage.unavailable)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function AssetCapacityBoard({
  taskStatus,
  onTaskChange,
}: Readonly<{
  taskStatus: DashboardTaskStatus;
  onTaskChange: (taskId: string | null) => void;
}>) {
  const selected = taskStatus.selected;
  const pipeline = taskStatus.pipeline;
  const stagesByCode = new Map(
    pipeline.stages.map((stage) => [stage.stage, stage]),
  );
  const qualityIssueCount =
    pipeline.qc.risk + pipeline.qc.rejected + (pipeline.qc.duplicate ?? 0);
  const qualityFindingCount = pipeline.qc.risk + pipeline.qc.rejected;
  const duplicateCount = pipeline.qc.duplicate ?? 0;
  const diagnosticTarget = selected?.actions.find(
    (action) => action.action === "VIEW_QC_ANOMALIES",
  )?.deepLink;

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
          <p>实时数据从采集、接收到发布的流转概览</p>
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
          const meta = stage
            ? stageMeta(stage, selected?.currentStage, pipeline.qc)
            : null;
          const tone = !stage
            ? undefined
            : stage.blocked + stage.failed + stage.unavailable > 0
              ? "danger"
              : stage.risk + stage.isolated > 0
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
            </li>
          );
        })}
      </ol>

      {selected && selected.blockerCount > 0 ? (
        <div className={styles.issueAlert} role="status">
          <ShieldCheck aria-hidden="true" size={18} />
          <span>
            当前任务有 {countFormatter.format(selected.blockerCount)}{" "}
            项技术或结构阻塞，请查看对应阶段状态。
          </span>
        </div>
      ) : null}

      {selected && qualityIssueCount > 0 ? (
        <div className={styles.issueAlert} data-tone="quality" role="status">
          <ShieldCheck aria-hidden="true" size={18} />
          <span>
            共 {countFormatter.format(qualityIssueCount)}{" "}
            个问题数据（质量风险或拒绝{" "}
            {countFormatter.format(qualityFindingCount)} 个，重复{" "}
            {countFormatter.format(duplicateCount)}{" "}
            个），已自动隔离，不阻塞其他数据包继续处理和入库。
          </span>
          {diagnosticTarget ? (
            <Link to={diagnosticTarget}>查看问题数据</Link>
          ) : null}
        </div>
      ) : !selected && qualityIssueCount > 0 ? (
        <div className={styles.issueAlert} data-tone="quality" role="status">
          <ShieldCheck aria-hidden="true" size={18} />
          <span>
            全部任务共有 {countFormatter.format(qualityIssueCount)}{" "}
            个问题数据（质量风险或拒绝{" "}
            {countFormatter.format(qualityFindingCount)} 个，重复{" "}
            {countFormatter.format(duplicateCount)}{" "}
            个），均已隔离，不阻塞正常数据流转。
          </span>
        </div>
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
