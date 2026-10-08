import { createViewerRendererCatalog } from "./renderer-catalog";
import type { ViewerRendererRegistration } from "./renderer-contract";

/** Retained rendering policies are registrations, rather than resolver modality branches. */
export const builtinViewerRenderers: readonly ViewerRendererRegistration[] = [
  {
    descriptor: {
      id: "video",
      version: "1",
      modalities: ["rgb"],
      schemas: "*",
      requiredInputs: [],
      priority: 10,
      minWidthPx: 320,
      aspectRatio: "16 / 9",
      resourceScope: "video",
    },
    default: true,
    implementation: { kind: "builtin", panelKind: "video" },
  },
  {
    descriptor: {
      id: "depth",
      version: "1",
      modalities: ["depth"],
      schemas: "*",
      requiredInputs: [],
      priority: 20,
      minWidthPx: 320,
      aspectRatio: "16 / 9",
      resourceScope: "video",
    },
    default: true,
    implementation: { kind: "builtin", panelKind: "depth" },
  },
  {
    descriptor: {
      id: "pointcloud",
      version: "1",
      modalities: ["pointcloud"],
      schemas: "*",
      requiredInputs: [],
      priority: 30,
      minWidthPx: 320,
      aspectRatio: "16 / 9",
      resourceScope: "pointcloud",
    },
    default: true,
    implementation: { kind: "builtin", panelKind: "pointcloud-preview" },
  },
  {
    descriptor: {
      id: "joints",
      version: "1",
      modalities: ["joint_state"],
      schemas: "*",
      requiredInputs: [],
      priority: 40,
      minWidthPx: 420,
      resourceScope: "curve",
      jointAxes: true,
    },
    default: true,
    implementation: { kind: "builtin", panelKind: "time-series" },
  },
  {
    descriptor: {
      id: "numeric",
      version: "1",
      modalities: ["action", "force", "pose", "imu", "tactile"],
      schemas: "*",
      requiredInputs: [],
      priority: 40,
      minWidthPx: 420,
      resourceScope: "curve",
    },
    default: true,
    implementation: { kind: "builtin", panelKind: "time-series" },
  },
  {
    descriptor: {
      id: "events",
      version: "1",
      modalities: ["event"],
      schemas: "*",
      requiredInputs: [],
      priority: 50,
      minWidthPx: 420,
      resourceScope: "curve",
    },
    default: true,
    implementation: { kind: "builtin", panelKind: "event-track" },
  },
];

export const defaultViewerRendererCatalog = createViewerRendererCatalog(
  builtinViewerRenderers,
);
