import type { VerificationRun } from '../../../features/ingest/upload/model';
import { validationStageOrder } from '../../../features/ingest/validation-pipeline';

function text(value: string | { readonly kind: 'UNKNOWN'; readonly raw: string }): string {
  return typeof value === 'string' ? value : `UNKNOWN (${value.raw})`;
}

export function ValidationPipeline({ run }: { readonly run: VerificationRun | null }) {
  if (!run) return <p>尚无校验运行。</p>;
  return <ol className="pipeline" aria-label="校验流水线">{[...run.stages].sort((left, right) => validationStageOrder(left.code) - validationStageOrder(right.code)).map((stage, index) => <li key={`${text(stage.code)}-${index}`} data-stage-status={text(stage.status)}><strong>{text(stage.code)}</strong><span>{text(stage.status)}</span>{stage.skipReason ? <small>{stage.skipReason}</small> : null}{stage.findingCount !== '0' ? <em>{stage.findingCount} 条发现</em> : null}</li>)}</ol>;
}
