import type {
  AuthorizationSnapshot,
  Capability,
} from "../../entities/capability";
import { dataAnnotationRoutes, dataUploadRoutes } from "./navigation-routes";

export type NavigationGroupId =
  | "dashboard"
  | "ingest"
  | "production"
  | "governance"
  | "security";

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
export type NavigationCapabilitySource =
  | ReadonlySet<string>
  | Pick<AuthorizationSnapshot, "capabilities">;

export const navigationManifest: NavigationManifest = [
  {
    groupId: "dashboard",
    label: "工作台",
    items: [
      {
        pageId: "P01",
        label: "工作台",
        path: "/dashboard",
        requiredCapability: null,
        activePatterns: ["/dashboard"],
      },
    ],
  },
  {
    groupId: "ingest",
    label: "采集与接收",
    items: [
      {
        pageId: "P20",
        label: "采集任务",
        path: "/collection-tasks",
        requiredCapability: null,
        activePatterns: ["/collection-tasks", "/collection-tasks/:taskId"],
      },
      {
        pageId: "P02",
        label: "数据源",
        path: "/ingest/sources",
        requiredCapability: "ingest_source.read",
        activePatterns: ["/ingest/sources"],
      },
      {
        pageId: "P03",
        label: "数据上传",
        path: dataUploadRoutes.newUpload,
        requiredCapability: "upload.read",
        activePatterns: [
          dataUploadRoutes.legacyIndex,
          dataUploadRoutes.newUpload,
          dataUploadRoutes.records,
          "/ingest/uploads/:uploadId",
        ],
      },
    ],
  },
  {
    groupId: "production",
    label: "数据生产",
    items: [
      {
        pageId: "P05",
        label: "数据集",
        path: "/datasets",
        requiredCapability: "dataset.read",
        activePatterns: [
          "/datasets",
          "/datasets/:datasetId",
          "/datasets/:datasetId/versions/:versionId",
          "/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view",
        ],
      },
      {
        pageId: "P08",
        label: "数据标注",
        path: dataAnnotationRoutes.annotate,
        requiredCapability: "annotation_task.read",
        activePatterns: [
          dataAnnotationRoutes.legacyIndex,
          dataAnnotationRoutes.annotate,
          dataAnnotationRoutes.revisions,
          dataAnnotationRoutes.tagReview,
          "/annotations/tasks/:taskId",
        ],
      },
      {
        pageId: "P09",
        label: "质量问题",
        path: "/manual/issues",
        requiredCapability: "manual_issue.read",
        activePatterns: ["/manual/issues"],
      },
      {
        pageId: "P10Q",
        label: "标注任务",
        path: "/annotation-tasks",
        requiredCapability: "annotation_task.read",
        activePatterns: ["/annotation-tasks"],
      },
    ],
  },
  {
    groupId: "governance",
    label: "治理与资产",
    items: [
      {
        pageId: "P12",
        label: "存储容量",
        path: "/storage/overview",
        requiredCapability: "storage.overview.read",
        activePatterns: ["/storage/overview"],
      },
      {
        pageId: "P13",
        label: "生命周期",
        path: "/storage/lifecycle",
        requiredCapability: "storage.lifecycle.read",
        activePatterns: ["/storage/lifecycle"],
      },
      {
        pageId: "P14",
        label: "机器人资产",
        path: "/settings/robot-models",
        requiredCapability: "robot_model.read",
        activePatterns: ["/settings/robot-models", "/settings/robots"],
      },
      {
        pageId: "P16",
        label: "标定管理",
        path: "/settings/calibrations",
        requiredCapability: "calibration.read",
        activePatterns: ["/settings/calibrations"],
      },
      {
        pageId: "P17",
        label: "Schema 管理",
        path: "/settings/data-schemas",
        requiredCapability: "data_schema.read",
        activePatterns: ["/settings/data-schemas"],
      },
    ],
  },
  {
    groupId: "security",
    label: "安全与审计",
    items: [
      {
        pageId: "P18",
        label: "账户与权限",
        path: "/settings/access",
        requiredCapability: "access.read",
        activePatterns: ["/settings/access"],
      },
      {
        pageId: "P19",
        label: "审计日志",
        path: "/settings/audit",
        requiredCapability: "audit.read",
        activePatterns: ["/settings/audit"],
      },
    ],
  },
] as const;

export function filterNavigationManifest(
  source: NavigationCapabilitySource,
  pageAvailability: PageAvailability,
): NavigationManifest {
  const capabilities =
    "capabilities" in source ? new Set(source.capabilities) : source;
  return navigationManifest.flatMap((group) => {
    const items = group.items.filter(
      (item) =>
        pageAvailability[item.pageId] === true &&
        (item.requiredCapability === null ||
          capabilities.has(item.requiredCapability)),
    );
    return items.length > 0 ? [{ ...group, items }] : [];
  });
}

export function resolveGroupLanding(
  groupId: NavigationGroupId,
  capabilities: NavigationCapabilitySource,
  pageAvailability: PageAvailability,
): string | null {
  return (
    filterNavigationManifest(capabilities, pageAvailability).find(
      (group) => group.groupId === groupId,
    )?.items[0]?.path ?? null
  );
}
