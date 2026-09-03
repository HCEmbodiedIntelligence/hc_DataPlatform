import type { AuthorizationSnapshot } from "../../entities/capability";
import { isPageHidden } from "../page-visibility";
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
  requiredCapability: string | null;
  administratorOnly: boolean;
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
        administratorOnly: false,
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
        administratorOnly: false,
        activePatterns: ["/collection-tasks", "/collection-tasks/:taskId"],
      },
      {
        pageId: "P02",
        label: "数据源",
        path: "/ingest/sources",
        requiredCapability: "ingest_source.read",
        administratorOnly: false,
        activePatterns: ["/ingest/sources"],
      },
      {
        pageId: "P03",
        label: "数据上传",
        path: dataUploadRoutes.newUpload,
        requiredCapability: "upload.read",
        administratorOnly: false,
        activePatterns: [
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
        pageId: "P23",
        label: "录制切片",
        path: "/recordings",
        requiredCapability: "episode.read",
        administratorOnly: false,
        activePatterns: ["/recordings", "/recordings/:recordingId/slice"],
      },
      {
        pageId: "P05",
        label: "数据集",
        path: "/datasets",
        requiredCapability: "dataset.read",
        administratorOnly: false,
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
        administratorOnly: false,
        activePatterns: [
          dataAnnotationRoutes.annotate,
          dataAnnotationRoutes.revisions,
          dataAnnotationRoutes.tagReview,
          "/annotations/tasks/:taskId",
        ],
      },
      {
        pageId: "P21",
        label: "数据导出",
        path: "/exports",
        requiredCapability: "export.read",
        administratorOnly: false,
        activePatterns: ["/exports"],
      },
      {
        pageId: "P09",
        label: "问题数据",
        path: "/manual/issues",
        requiredCapability: "manual_issue.read",
        administratorOnly: false,
        activePatterns: [
          "/manual/issues",
          "/manual/issues/raw-diagnostic/:uploadId",
        ],
      },
      {
        pageId: "P10Q",
        label: "标注任务",
        path: "/annotation-tasks",
        requiredCapability: "annotation_task.read",
        administratorOnly: false,
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
        administratorOnly: true,
        activePatterns: ["/storage/overview"],
      },
      {
        pageId: "P13",
        label: "生命周期",
        path: "/storage/lifecycle",
        requiredCapability: "storage.lifecycle.read",
        administratorOnly: true,
        activePatterns: ["/storage/lifecycle"],
      },
      {
        pageId: "P14",
        label: "机器人模型",
        path: "/settings/robot-models",
        requiredCapability: "robot_model.read",
        administratorOnly: true,
        activePatterns: ["/settings/robot-models", "/settings/robots"],
      },
      {
        pageId: "P15",
        label: "机器人实例",
        path: "/settings/robot-instances",
        requiredCapability: "robot.read",
        administratorOnly: true,
        activePatterns: ["/settings/robot-instances"],
      },
      {
        pageId: "P16",
        label: "标定管理",
        path: "/settings/calibrations",
        requiredCapability: "calibration.read",
        administratorOnly: true,
        activePatterns: ["/settings/calibrations"],
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
        administratorOnly: true,
        activePatterns: ["/settings/access"],
      },
      {
        pageId: "P19",
        label: "审计日志",
        path: "/settings/audit",
        requiredCapability: "audit.read",
        administratorOnly: true,
        activePatterns: ["/settings/audit"],
      },
      {
        pageId: "P22",
        label: "平台设置",
        path: "/settings/platform-operations",
        requiredCapability: "platform.operations.read",
        administratorOnly: true,
        activePatterns: ["/settings/platform-operations"],
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
  const platformAdministrator = capabilities.has("platform.admin");
  return navigationManifest.flatMap((group) => {
    const items = group.items.filter(
      (item) =>
        !isPageHidden(item.pageId) &&
        pageAvailability[item.pageId] === true &&
        (!item.administratorOnly ||
          platformAdministrator ||
          (item.requiredCapability !== null &&
            capabilities.has(item.requiredCapability))),
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
