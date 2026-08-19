import {
  Activity,
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
import type {
  DashboardPendingPage,
  DashboardSignalStage,
  DashboardSnapshot,
} from "../../../features/dashboard/types";
import { StatusTag } from "../../../shared/ui";
import { DashboardSectionNotice, sectionTone } from "./DashboardSectionNotice";
import styles from "./AssetCapacityBoard.module.css";

const stagePresentation: Readonly<
  Record<
    DashboardSignalStage,
    Readonly<{
      label: string;
      eyebrow: string;
      Icon: LucideIcon;
    }>
  >
> = {
  COLLECTED: { label: "采集", eyebrow: "SAVED", Icon: Camera },
  RECEIVED: { label: "已接收", eyebrow: "RECEIVED", Icon: CloudDownload },
  AUTO_QC: { label: "自动质检", eyebrow: "AUTO QC", Icon: ShieldCheck },
  ALIGNED_30_HZ: { label: "30 Hz 对齐", eyebrow: "ALIGNED", Icon: Activity },
  LANCE: { label: "Lance", eyebrow: "LANCE", Icon: Database },
  ANNOTATION: { label: "数据标注", eyebrow: "ANNOTATION", Icon: Tags },
  REVIEW: { label: "审核", eyebrow: "REVIEW", Icon: UserRoundCheck },
  PUBLISHED: { label: "已发布", eyebrow: "PUBLISHED", Icon: Send },
};

export function AssetCapacityBoard({
  snapshot,
  pending,
}: Readonly<{
  snapshot: DashboardSnapshot;
  pending?: DashboardPendingPage;
}>) {
  const publication = snapshot.signalPipeline.publishedRegion;
  const qcAnomalies =
    pending?.items.filter((item) => item.kind === "QC_ANOMALY") ?? [];
  const rawDiagnosticTarget = qcAnomalies.find((item) => item.target.deep_link)
    ?.target.deep_link;

  return (
    <section
      className={styles.panel}
      aria-labelledby="signal-pipeline-title"
      data-section-status={snapshot.signalPipeline.status}
    >
      <header className={styles.header}>
        <div className={styles.heading}>
          <div className={styles.titleLine}>
            <h2 id="signal-pipeline-title">信号轨道</h2>
            <StatusTag
              status={snapshot.signalPipeline.status}
              label={snapshot.signalPipeline.status}
              known
              tone={sectionTone(snapshot.signalPipeline.status)}
            />
          </div>
          <p>实时数据流转概览 · SAVED 与云端已接收分别表达</p>
        </div>
      </header>

      <ol className={styles.rail} aria-label="采集到发布的固定八阶段">
        {snapshot.signalPipeline.stages.map((stage) => {
          const { label, eyebrow, Icon } = stagePresentation[stage];
          const isPublished = stage === "PUBLISHED";
          const section = isPublished ? publication : snapshot.signalPipeline;
          return (
            <li key={stage} className={styles.stage} data-stage={stage}>
              <span className={styles.stageLabel}>{label}</span>
              <span className={styles.stageIcon} aria-hidden="true">
                <Icon size={20} strokeWidth={1.8} />
              </span>
              <strong>{eyebrow}</strong>
              {stage === "AUTO_QC" && qcAnomalies.length > 0 ? (
                <span className={styles.stageFact} data-tone="danger">
                  当前窗口异常 {qcAnomalies.length}
                </span>
              ) : isPublished && publication.publicationCount !== null ? (
                <span className={styles.stageFact}>
                  已发布 {publication.publicationCount}
                </span>
              ) : (
                <span className={styles.stageFact}>{section.status}</span>
              )}
              {isPublished && publication.lineageCount !== null ? (
                <span className={styles.stageMeta}>
                  血缘 {publication.lineageCount}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>

      {qcAnomalies.length > 0 ? (
        <div className={styles.qcAlert} role="status">
          <ShieldCheck aria-hidden="true" size={18} />
          <span>
            当前待办窗口发现 {qcAnomalies.length} 条自动质检异常；异常保留在
            Raw。
          </span>
          {rawDiagnosticTarget ? (
            <Link to={rawDiagnosticTarget}>查看 Raw 诊断</Link>
          ) : null}
        </div>
      ) : null}

      <DashboardSectionNotice
        section={snapshot.signalPipeline}
        label="信号轨道"
        compact
      />
      {publication.unresolvedHistoryCount > 0 ? (
        <div className={styles.lineageWarning} role="status">
          有 {publication.unresolvedHistoryCount}{" "}
          条历史发布记录尚未解析区域血缘。
        </div>
      ) : null}
    </section>
  );
}
