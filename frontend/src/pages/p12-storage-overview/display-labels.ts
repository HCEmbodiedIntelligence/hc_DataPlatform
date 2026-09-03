const OBJECT_ROLE_LABELS: Readonly<Record<string, string>> = {
  SOURCE: '原始 MCAP',
  DERIVED: 'Lance 加工数据',
  PREVIEW: '临时预览缓存',
  EXPORT: '发布与导出成果',
  ROBOT_ASSET: '机器人资源',
  UNCLASSIFIED: '未分类',
  UNKNOWN: '未知类型',
};

const STORAGE_CLASS_LABELS: Readonly<Record<string, string>> = {
  STANDARD: '标准存储',
  IA: '低频访问',
  ARCHIVE: '归档存储',
  COLD_ARCHIVE: '冷归档存储',
  MIXED: '混合存储',
  UNKNOWN: '未知层级',
};

const OBJECT_STATUS_LABELS: Readonly<Record<string, string>> = {
  AVAILABLE: '可用',
  PARTIAL: '部分可用',
  RESTORING: '恢复中',
  MIGRATING: '迁移中',
  DELETING: '删除中',
  DELETED: '已删除',
  MISSING: '文件缺失',
  QUARANTINED: '已隔离',
  UNAVAILABLE: '不可用',
  UNKNOWN: '状态未知',
};

const MULTIPART_STATUS_LABELS: Readonly<Record<string, string>> = {
  ACTIVE: '上传中',
  PAUSED_RESUMABLE: '已暂停，可继续',
  INACTIVE: '长时间无活动',
  USER_CANCELLED: '用户已取消',
  COMPLETING: '正在合并分片',
  VALIDATING: '正在校验',
  ABORTING: '正在清理',
  ABORT_FAILED: '清理失败',
  UNKNOWN: '状态未知',
};

const FRESHNESS_LABELS: Readonly<Record<string, string>> = {
  CURRENT: '最新',
  STALE: '需要更新',
  EXPIRED: '已失效',
  UNKNOWN: '状态未知',
};

const RECONCILIATION_STATUS_LABELS: Readonly<Record<string, string>> = {
  MATCHED: '核对一致',
  HAS_GAP: '存在差异',
  INCOMPLETE: '数据不完整',
  RUNNING: '正在核对',
  UNKNOWN: '状态未知',
};

const PROTECTION_REASON_LABELS: Readonly<Record<string, string>> = {
  ACTIVE_REFERENCE: '仍被数据引用',
  RETENTION: '仍在保留期内',
  LEGAL_HOLD: '已设置合规保留',
  PERMISSION: '权限限制',
  CONCURRENT_JOB: '相关任务正在运行',
};

function labelOf(labels: Readonly<Record<string, string>>, value: string, fallback: string): string {
  return labels[value] ?? fallback;
}

export function objectRoleLabel(value: string): string {
  return labelOf(OBJECT_ROLE_LABELS, value, '其他类型');
}

export function storageClassLabel(value: string): string {
  return labelOf(STORAGE_CLASS_LABELS, value, '其他存储层级');
}

export function objectStatusLabel(value: string): string {
  return labelOf(OBJECT_STATUS_LABELS, value, '其他状态');
}

export function multipartStatusLabel(value: string): string {
  return labelOf(MULTIPART_STATUS_LABELS, value, '其他状态');
}

export function freshnessLabel(value: string): string {
  return labelOf(FRESHNESS_LABELS, value, '状态未知');
}

export function reconciliationStatusLabel(value: string): string {
  return labelOf(RECONCILIATION_STATUS_LABELS, value, '其他状态');
}

export function protectionReasonLabel(value: string): string {
  return labelOf(PROTECTION_REASON_LABELS, value, '受系统保护');
}

export function formatChineseDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}
