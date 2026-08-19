import type { DashboardCoverage } from "../../../features/dashboard/types";
import { StatusTag } from "../../../shared/ui";
import { DashboardSectionNotice, sectionTone } from "./DashboardSectionNotice";
import styles from "../styles.module.css";

export function DashboardCoverageTable({
  coverage,
}: Readonly<{ coverage: DashboardCoverage }>) {
  return (
    <div className={styles.coverageState}>
      <div>
        <strong>采集覆盖率</strong>
        <span>版本化计划与分母未完整前不计算数值</span>
      </div>
      <StatusTag
        status={coverage.section.status}
        label={coverage.section.status}
        known
        tone={sectionTone(coverage.section.status)}
      />
      <DashboardSectionNotice
        section={coverage.section}
        label="覆盖率"
        compact
      />
    </div>
  );
}
