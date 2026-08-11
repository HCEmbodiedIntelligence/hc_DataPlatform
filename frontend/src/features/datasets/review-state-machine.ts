import type {
  DatasetVersionDeliveryStatus,
  DatasetVersionStatus,
} from '../../entities/dataset-version';

export type ReviewMutation = 'APPROVE' | 'RETURN' | 'RETRY_MANIFEST';

export type ReviewStatePolicy = Readonly<{
  readOnly: boolean;
  unknownEnum: boolean;
  allowedMutations: readonly ReviewMutation[];
  reason: string;
}>;

export function getReviewStatePolicy(
  status: DatasetVersionStatus,
  deliveryStatus?: DatasetVersionDeliveryStatus,
): ReviewStatePolicy {
  if (status === 'UNKNOWN' || deliveryStatus === 'UNKNOWN') {
    return {
      readOnly: true,
      unknownEnum: true,
      allowedMutations: [],
      reason: '未知版本状态；已按只读模式关闭全部复核写操作。',
    };
  }
  if (status === 'RETURNED') {
    return {
      readOnly: true,
      unknownEnum: false,
      allowedMutations: [],
      reason: '退回版本是终态，只能打开服务端创建的后继草稿。',
    };
  }
  if (status === 'READY') {
    return {
      readOnly: true,
      unknownEnum: false,
      allowedMutations: [],
      reason: 'Ready 版本及其 Manifest 已永久冻结。',
    };
  }
  if (deliveryStatus === 'FAILED') {
    return {
      readOnly: false,
      unknownEnum: false,
      allowedMutations: ['RETRY_MANIFEST'],
      reason: 'Manifest 生成失败；重新预检后可受控重试。',
    };
  }
  if (deliveryStatus === 'GENERATING' || deliveryStatus === 'CANDIDATE_READY') {
    return {
      readOnly: true,
      unknownEnum: false,
      allowedMutations: [],
      reason: 'Manifest 正在生成或等待服务端原子发布。',
    };
  }
  return {
    readOnly: false,
    unknownEnum: false,
    allowedMutations: ['APPROVE', 'RETURN'],
    reason: '复核中；最终动作仍需 capability、allowed_actions、If-Match 与二次确认。',
  };
}

export function canRunReviewMutation(policy: ReviewStatePolicy, mutation: ReviewMutation): boolean {
  return policy.allowedMutations.includes(mutation);
}
