import {
  Database,
  FilePenLine,
  Layers3,
  PackageCheck,
  Radio,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react';
import {
  DATA_ASSET_STAGES,
  PREVIEW_POLICY,
  QUALITY_OUTCOMES,
  type DataAssetStageId,
} from '../../../features/data-governance/model';
import styles from './DataLifecycleRail.module.css';

const stageIcons: Readonly<Record<DataAssetStageId, LucideIcon>> = {
  raw: Database,
  verified: ShieldCheck,
  'base-lance': Layers3,
  'annotation-view': FilePenLine,
  published: PackageCheck,
};

export function DataLifecycleRail() {
  return (
    <section className={styles.panel} aria-labelledby="data-lifecycle-title">
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>DATA PROVENANCE</span>
          <h2 id="data-lifecycle-title">数据资产链路</h2>
        </div>
        <p>资产阶段与质量判定分开表达，任何编辑都不覆盖原始事实。</p>
      </header>

      <ol className={styles.rail}>
        {DATA_ASSET_STAGES.map((stage) => {
          const Icon = stageIcons[stage.id];
          return (
            <li key={stage.id} className={styles.stage} data-stage={stage.id}>
              <span className={styles.stageIcon} aria-hidden="true">
                <Icon size={17} strokeWidth={1.8} />
              </span>
              <div className={styles.stageCopy}>
                <code translate="no">{stage.code}</code>
                <h3>{stage.title}</h3>
                <p>{stage.description}</p>
                <span className={styles.stagePolicy}>{stage.policy}</span>
              </div>
            </li>
          );
        })}
      </ol>

      <footer className={styles.footer}>
        <div className={styles.previewPolicy}>
          <Radio aria-hidden="true" size={17} strokeWidth={1.8} />
          <div>
            <strong>{PREVIEW_POLICY.title}</strong>
            <span>{PREVIEW_POLICY.description}</span>
          </div>
        </div>
        <ul className={styles.qualityLegend} aria-label="质量判定">
          {QUALITY_OUTCOMES.map((outcome) => (
            <li key={outcome.code} data-tone={outcome.tone}>
              <strong translate="no">{outcome.code}</strong>
              <span>{outcome.description}</span>
            </li>
          ))}
        </ul>
      </footer>
    </section>
  );
}
