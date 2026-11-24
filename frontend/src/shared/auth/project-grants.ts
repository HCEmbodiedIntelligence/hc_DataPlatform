export const grantOptions = [
  { value: "role.observer", label: "L1 项目观察员 · 查看进度与版本摘要" },
  { value: "role.collector", label: "L2 采集员 · 上传、导入与处理结果" },
  { value: "role.annotator", label: "L2 清洗标注员 · 领取、编辑与提交" },
  { value: "role.reviewer", label: "L3 质检员 · 复核、退回与通过" },
  { value: "role.publisher", label: "L3 发布员 · 发布数据集版本" },
  { value: "role.project_admin", label: "L4 项目管理员 · 成员、任务与审计" },
  { value: "episode.read", label: "Episode 预览" },
  { value: "export.read", label: "查看交付任务" },
  { value: "export.create", label: "创建交付任务" },
  { value: "export.download", label: "下载交付包" },
  { value: "dataset_version.download_raw", label: "下载原始文件" },
  { value: "dataset_version.download_manifest", label: "下载清单" },
  { value: "training.read", label: "查看训练任务" },
  { value: "training.create", label: "启动训练" },
  { value: "robot.read", label: "查看机器人" },
  { value: "teleoperation.connect", label: "连接指定机器人" },
  { value: "collection_task.delete", label: "删除指定采集任务及数据" },
  { value: "dataset.delete", label: "指定数据集删除预检" },
  { value: "dataset_version.delete", label: "指定数据版本删除预检" },
  { value: "episode.derive", label: "生成派生数据" },
  { value: "manual_issue.resolve", label: "质量问题处理权限" },
  { value: "manual_issue.triage", label: "质量问题分诊权限" },
  { value: "audit.export", label: "导出项目审计" },
  { value: "access.download_approval.manage", label: "审批项目数据交付" },
  { value: "tag.manage", label: "维护组织标签库" },
  { value: "quality.manage", label: "维护质量配置" },
  { value: "ingest_source.manage", label: "维护数据源" },
  { value: "robot.manage", label: "维护设备与采集方案" },
  { value: "data_schema.publish", label: "发布数据结构" },
  { value: "storage.lifecycle.manage", label: "管理存储策略" },
  { value: "storage.lifecycle.approve", label: "批准存储策略" },
  { value: "storage.lifecycle.execute", label: "执行存储策略" },
] as const;

export function grantLabel(key: string) {
  return grantOptions.find(option => option.value === key)?.label ?? key;
}

export interface GrantTerms {
  expires_at?: string | null;
  resource_id?: string | null;
  replaces_request_id?: string | null;
}
