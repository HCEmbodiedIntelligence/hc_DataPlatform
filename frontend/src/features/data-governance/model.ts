export type DataAssetStageId =
  | 'raw'
  | 'verified'
  | 'base-lance'
  | 'annotation-view'
  | 'published';

export type DataAssetStage = Readonly<{
  id: DataAssetStageId;
  code: 'RAW' | 'RAW_VERIFIED' | 'BASE_LANCE' | 'ANNOTATION_VIEW' | 'PUBLISHED';
  title: string;
  description: string;
  policy: string;
}>;

export const DATA_ASSET_STAGES: readonly DataAssetStage[] = [
  {
    id: 'raw',
    code: 'RAW',
    title: '原始数据',
    description: 'MCAP 与数据清单原样保留，作为可追溯事实源。',
    policy: '不可覆盖',
  },
  {
    id: 'verified',
    code: 'RAW_VERIFIED',
    title: '完整性通过',
    description: '大小、CRC64、SHA-256 与结构校验均已完成。',
    policy: '质量门禁',
  },
  {
    id: 'base-lance',
    code: 'BASE_LANCE',
    title: 'Lance 基线',
    description: '完成时序对齐与派生校验，形成稳定基线。',
    policy: '不可改写',
  },
  {
    id: 'annotation-view',
    code: 'ANNOTATION_VIEW',
    title: '标注视图',
    description: '以 Revision 和半开区间记录排除，不修改基线。',
    policy: '非破坏性',
  },
  {
    id: 'published',
    code: 'PUBLISHED',
    title: '发布版本',
    description: '冻结 Lance、标注版本、质量规则与纳入范围。',
    policy: '冻结不可变',
  },
] as const;

export type QualityOutcomeCode = 'PASS' | 'RISK' | 'REJECT';

export type QualityOutcome = Readonly<{
  code: QualityOutcomeCode;
  label: string;
  description: string;
  tone: 'success' | 'warning' | 'danger';
}>;

export const QUALITY_OUTCOMES: readonly QualityOutcome[] = [
  {
    code: 'PASS',
    label: '通过',
    description: '可进入训练数据生成',
    tone: 'success',
  },
  {
    code: 'RISK',
    label: '风险',
    description: '隔离并进入人工复核',
    tone: 'warning',
  },
  {
    code: 'REJECT',
    label: '拒绝',
    description: '保留 Raw，但不进入训练',
    tone: 'danger',
  },
] as const;

export const PREVIEW_POLICY = {
  code: 'EPHEMERAL_PREVIEW',
  title: '按需预览',
  description: '临时 HLS / fMP4 缓存，仅用于查看，不是正式资产或训练来源。',
} as const;
