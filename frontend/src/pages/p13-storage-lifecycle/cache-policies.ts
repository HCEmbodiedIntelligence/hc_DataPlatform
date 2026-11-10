export const cachePolicies = {
  CLEAN_INVENTORY_DETAILS: { label: '容量盘点历史明细', days: 0, clock: '盘点生成后', detail: '0 天表示只保留最新一轮完整明细；填写天数可保留近期明细。旧明细先校验并保存容量汇总，再回收数据库记录，不删除实际文件。' },
  CLEAN_INVENTORY_HISTORY: { label: '容量历史汇总', days: 365, clock: '盘点生成后', detail: '保留容量变化趋势与分类统计；到期仅回收已精简明细的旧汇总，最新一轮始终保留。不会删除业务数据、文件或审计记录。' },
  CLEAN_PROCESSING_CACHE: { label: '解析中间缓存', days: 7, clock: '入库完成后', detail: '所有成片入库完成后开始计时；核验原始文件和重建结果后回收选段 MCAP、解析视频。必要标记、传感器和处理凭据继续保留。失败任务从最后失败时间计时。' },
  CLEAN_RECORDING_PREVIEWS: { label: '原始录制预览', days: 30, clock: '最近使用后', detail: '本地录制播放与切片预览，最近播放或授权后重新计时；原始数据可用时到期回收，下次访问重新生成。' },
  CLEAN_EPISODE_PREVIEWS: { label: '成片播放预览', days: 30, clock: '最近使用后', detail: '本地成片播放缓存，最近播放或授权后重新计时；正式数据包完整时到期回收，下次播放重新生成。' },
  CLEAN_EXPORT_ARCHIVES: { label: '数据导出包', days: 30, clock: '最近使用后', detail: '本地 ZIP 缓存，最近下载或授权后重新计时；冻结的导出记录、数据版本及标注继续保留，下次下载重新打包。' },
  CLEAN_PROCESSING_STAGING: { label: '处理暂存与源文件缓存', days: 7, clock: '最近处理后', detail: '本地下载、解析及对齐暂存；被任务锁定的源文件保留。共享缓存采用所有所属项目中最长保留期，任一项目未启用策略时保留。' },
  CLEAN_MEDIA_STAGING: { label: '媒体生成暂存', days: 7, clock: '最后写入后', detail: '视频生成暂存；共享目录采用所有所属项目中最长保留期，任一项目未启用策略时保留。正式视频不在此范围。' },
  CLEAN_OSS_TEST_COPIES: { label: '接入测试副本', days: 7, clock: '测试副本登记后', detail: '仅处理已登记归属和正式原件的测试副本；校验原件与副本的大小和 SHA-256 一致后回收，不删除正式原件。' },
} as const;
export type CacheAction = keyof typeof cachePolicies;
export function isCacheAction(value: string | undefined): value is CacheAction {
  return !!value && Object.hasOwn(cachePolicies, value);
}
export function cacheRule(value: string) {
  return isCacheAction(value) ? cachePolicies[value] : undefined;
}
