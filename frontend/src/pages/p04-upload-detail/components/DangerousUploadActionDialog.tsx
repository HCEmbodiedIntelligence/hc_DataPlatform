import type { BlockedReason } from '../../../entities/data-source';
import {
  DangerConfirmModal,
  type DangerConflict,
  type DangerPreflightEvidence,
} from '../../../shared/ui';

export function DangerousUploadActionDialog(props: {
  readonly open: boolean;
  readonly title: string;
  readonly uploadId: string;
  readonly impact: string;
  readonly blockedReasons: readonly BlockedReason[];
  readonly preflight: DangerPreflightEvidence | null;
  readonly currentScopeKey: string;
  readonly conflict?: DangerConflict | null;
  readonly pending: boolean;
  readonly onClose: () => void;
  readonly onResolveConflict?: (status: 409 | 412) => void;
  readonly onConfirm: (reason: string) => void;
}) {
  return (
    <DangerConfirmModal
      open={props.open}
      title={props.title}
      actionLabel="确认执行"
      resourceId={props.uploadId}
      impact={props.impact}
      blockers={props.blockedReasons}
      preflight={props.preflight}
      currentScopeKey={props.currentScopeKey}
      confirmation={{ expectedText: props.uploadId }}
      conflict={props.conflict}
      pending={props.pending}
      onCancel={props.onClose}
      onResolveConflict={props.onResolveConflict}
      onConfirm={() => props.onConfirm('用户确认复验并申请释放隔离')}
    />
  );
}
