import { Alert, Steps, Typography } from 'antd';
import type { VerificationRun } from '../../../features/ingest/upload/model';
import { validationStageOrder } from '../../../features/ingest/validation-pipeline';

function text(value: string | { readonly kind: 'UNKNOWN'; readonly raw: string }): string {
  return typeof value === 'string' ? value : `UNKNOWN (${value.raw})`;
}

function stepStatus(value: string): 'wait' | 'process' | 'finish' | 'error' {
  if (value === 'PASSED') return 'finish';
  if (value === 'RUNNING') return 'process';
  if (value === 'FAILED' || value === 'CANCELLED') return 'error';
  return 'wait';
}

export function ValidationPipeline({ run }: { readonly run: VerificationRun | null }) {
  if (!run) return <Alert type="info" showIcon title="尚无校验运行。" />;
  const stages = [...run.stages].sort(
    (left, right) => validationStageOrder(left.code) - validationStageOrder(right.code),
  );
  return (
    <Steps
      orientation="vertical"
      responsive
      aria-label="校验流水线"
      items={stages.map((stage, index) => {
        const code = text(stage.code);
        const status = text(stage.status);
        return {
          key: `${code}-${index}`,
          title: code,
          status: stepStatus(status),
          content: (
            <span data-stage-status={status}>
              <Typography.Text>{status}</Typography.Text>
              {stage.skipReason ? <><br /><Typography.Text type="secondary">{stage.skipReason}</Typography.Text></> : null}
              {stage.findingCount !== '0' ? <><br /><Typography.Text type="danger">{stage.findingCount} 条发现</Typography.Text></> : null}
            </span>
          ),
        };
      })}
    />
  );
}
