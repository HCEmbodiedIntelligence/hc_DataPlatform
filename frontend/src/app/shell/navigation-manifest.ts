import type { AuthorizationSnapshot, Capability } from '../../entities/capability';

export type NavigationGroupId =
  | 'dashboard'
  | 'ingest'
  | 'datasets'
  | 'annotation'
  | 'manual'
  | 'storage'
  | 'settings';

export interface NavigationItem {
  pageId: string;
  label: string;
  path: string;
  requiredCapability: Capability | null;
  activePatterns: readonly string[];
}

export interface NavigationGroup {
  groupId: NavigationGroupId;
  label: string;
  items: readonly NavigationItem[];
}

export type NavigationManifest = readonly NavigationGroup[];
export type PageAvailability = Readonly<Record<string, boolean>>;
export type NavigationCapabilitySource = ReadonlySet<string> | Pick<AuthorizationSnapshot, 'capabilities'>;

export const navigationManifest: NavigationManifest = [
  {
    groupId: 'dashboard',
    label: '工作台',
    items: [
      { pageId: 'P01', label: '工作台', path: '/dashboard', requiredCapability: null, activePatterns: ['/dashboard'] },
    ],
  },
  {
    groupId: 'ingest',
    label: '数据接入',
    items: [
      { pageId: 'P03', label: '上传任务', path: '/ingest/uploads', requiredCapability: 'upload.read', activePatterns: ['/ingest/uploads', '/ingest/uploads/:uploadId'] },
      { pageId: 'P02', label: '数据源', path: '/ingest/sources', requiredCapability: 'ingest_source.read', activePatterns: ['/ingest/sources'] },
    ],
  },
  {
    groupId: 'datasets',
    label: '数据资产',
    items: [
      { pageId: 'P05', label: '数据集', path: '/datasets', requiredCapability: 'dataset.read', activePatterns: ['/datasets', '/datasets/:datasetId', '/datasets/:datasetId/versions/:versionId', '/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view'] },
    ],
  },
  {
    groupId: 'annotation',
    label: '数据标注',
    items: [
      { pageId: 'P08', label: '数据标注', path: '/annotations', requiredCapability: 'annotation_task.read', activePatterns: ['/annotations', '/annotations/tasks/:taskId'] },
    ],
  },
  {
    groupId: 'manual',
    label: '手动清洗',
    items: [
      { pageId: 'P09', label: '人工问题', path: '/manual/issues', requiredCapability: 'manual_issue.read', activePatterns: ['/manual/issues'] },
      { pageId: 'P10', label: '清洗草稿', path: '/manual/drafts', requiredCapability: 'cleaning.read', activePatterns: ['/manual/drafts', '/manual/drafts/:draftId'] },
    ],
  },
  {
    groupId: 'storage',
    label: '存储管理',
    items: [
      { pageId: 'P12', label: '存储容量', path: '/storage/overview', requiredCapability: 'storage.overview.read', activePatterns: ['/storage/overview'] },
      { pageId: 'P13', label: '生命周期', path: '/storage/lifecycle', requiredCapability: 'storage.lifecycle.read', activePatterns: ['/storage/lifecycle'] },
    ],
  },
  {
    groupId: 'settings',
    label: '系统管理',
    items: [
      { pageId: 'P14', label: '模型资产', path: '/settings/robot-models', requiredCapability: 'robot_model.read', activePatterns: ['/settings/robot-models'] },
      { pageId: 'P15', label: '机器人与组件', path: '/settings/robots', requiredCapability: 'robot.read', activePatterns: ['/settings/robots'] },
      { pageId: 'P16', label: '标定管理', path: '/settings/calibrations', requiredCapability: 'calibration.read', activePatterns: ['/settings/calibrations'] },
      { pageId: 'P17', label: '数据 Schema', path: '/settings/data-schemas', requiredCapability: 'data_schema.read', activePatterns: ['/settings/data-schemas'] },
      { pageId: 'P18', label: '用户权限', path: '/settings/access', requiredCapability: 'access.read', activePatterns: ['/settings/access'] },
      { pageId: 'P19', label: '审计日志', path: '/settings/audit', requiredCapability: 'audit.read', activePatterns: ['/settings/audit'] },
    ],
  },
] as const;

export function filterNavigationManifest(
  source: NavigationCapabilitySource,
  pageAvailability: PageAvailability,
): NavigationManifest {
  const capabilities = 'capabilities' in source ? new Set(source.capabilities) : source;
  return navigationManifest.flatMap((group) => {
    const items = group.items.filter(
      (item) =>
        pageAvailability[item.pageId] === true &&
        (item.requiredCapability === null || capabilities.has(item.requiredCapability)),
    );
    return items.length > 0 ? [{ ...group, items }] : [];
  });
}

export function resolveGroupLanding(
  groupId: NavigationGroupId,
  capabilities: NavigationCapabilitySource,
  pageAvailability: PageAvailability,
): string | null {
  return filterNavigationManifest(capabilities, pageAvailability).find((group) => group.groupId === groupId)
    ?.items[0]?.path ?? null;
}
