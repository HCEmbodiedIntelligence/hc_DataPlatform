import { Alert, Steps, Typography } from 'antd';
import { QUALITY_OUTCOMES } from '../../../features/data-governance/model';
import type { VerificationRun } from '../../../features/ingest/upload/model';
import {
  validationStageLabel,
  validationStageOrder,
  validationStageSkipReasonLabel,
  validationStageStatusLabel,
} from '../../../features/ingest/validation-pipeline';
import styles from './ValidationPipeline.module.css';

function stepStatus(value: string): 'wait' | 'process' | 'finish' | 'error' {
  if (value === 'PASSED') return 'finish';
  if (value === 'RUNNING') return 'process';
  if (value === 'FAILED' || value === 'CANCELLED') return 'error';
  return 'wait';
}

function QualityGatePolicy() {
  return (
    <aside className={styles.policy} aria-label="质量判定规则">
      <div className={styles.policyCopy}>
        <strong>质量判定独立于校验执行状态</strong>
        <span>
          Manifest 是上传完成标记；对象、摘要、解析与语义校验完成后，仍需单独给出训练准入结论。
        </span>
      </div>
      <ul className={styles.outcomes}>
        {QUALITY_OUTCOMES.map((outcome) => (
          <li key={outcome.code} data-tone={outcome.tone}>
            <strong translate="no">
              {outcome.code} · {outcome.label}
            </strong>
            <span>{outcome.description}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function ValidationPipeline({ run }: { readonly run: VerificationRun | null }) {
  const stages = run
    ? [...run.stages].sort(
        (left, right) => validationStageOrder(left.code) - validationStageOrder(right.code),
      )
    : [];

  return (
    <section className={styles.pipeline} aria-label="校验流水线">
      <QualityGatePolicy />
      {!run ? (
        <Alert
          type="info"
          showIcon
          title="尚无校验运行"
          description="Manifest 提交后，这里会依次展示完整性、解析、语义与原子可用性校验。"
        />
      ) : (
        <div className={styles.stepsSurface}>
          <Steps
            orientation="vertical"
            responsive
            items={stages.map((stage, index) => {
              const code = typeof stage.code === 'string' ? stage.code : stage.code.raw;
              const status = typeof stage.status === 'string' ? stage.status : stage.status.raw;
              return {
                key: `${code}-${index}`,
                title: validationStageLabel(stage.code),
                status: stepStatus(status),
                content: (
                  <span data-stage-status={status}>
                    <Typography.Text>
                      {validationStageStatusLabel(stage.status)}
                    </Typography.Text>
                    {stage.skipReason ? (
                      <>
                        <br />
                        <Typography.Text type="secondary">
                          {validationStageSkipReasonLabel(stage.skipReason)}
                        </Typography.Text>
                      </>
                    ) : null}
                    {stage.findingCount !== '0' ? (
                      <>
                        <br />
                        <Typography.Text type="danger">
                          {stage.findingCount} 条发现
                        </Typography.Text>
                      </>
                    ) : null}
                  </span>
                ),
              };
            })}
          />
        </div>
      )}
    </section>
  );
}
