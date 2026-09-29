import type { ReactNode } from "react";
import type { PlaybackClock } from "./PlaybackClock";
import type { RobotSceneCoreProps } from "./RobotSceneCore";
import type {
  ViewerPanelRenderer,
  ViewerTimelineSelection,
  ViewerTimelineTrack,
} from "./EpisodeWorkbenchCore";
import type { DomainError, StreamDescriptor } from "./types";

export type DataVisualizationWorkbenchMode =
  | "raw-diagnostic"
  | "lance-readonly"
  | "annotation"
  | "revision"
  | "tag-review"
  | "episode-slicing"
  | "cleaning"
  | "published-readonly";

export interface WorkbenchFact {
  readonly label: string;
  readonly value: string;
  readonly technical?: boolean;
}

export interface WorkbenchCollectionItem {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly status: string;
  readonly statusTone?: "neutral" | "info" | "warning" | "error" | "success";
  readonly facts?: readonly WorkbenchFact[];
}

export interface WorkbenchFinding {
  readonly id: string;
  readonly title: string;
  readonly severity: "info" | "warning" | "error";
  readonly streamLabel?: string;
  readonly topic?: string;
  readonly startNs: string;
  readonly endNs?: string;
  readonly message: string;
  readonly observed?: string;
  readonly threshold?: string;
}

export type WorkbenchActionKind =
  | "preserve-evidence"
  | "request-recollection"
  | "run-automated-check"
  | "custom";

export interface WorkbenchAction {
  readonly id: string;
  readonly kind: WorkbenchActionKind;
  readonly label: string;
  readonly disabledReason?: string;
  readonly invoke?: () => void | Promise<void>;
}

export interface WorkbenchDiagnosticNotes {
  readonly value: string;
  readonly maxLength?: number;
  readonly readOnly?: boolean;
  readonly onChange?: (value: string) => void;
}

export interface DataVisualizationWorkbenchAdapter {
  readonly mode: DataVisualizationWorkbenchMode;
  readonly id: string;
  readonly title: string;
  readonly description?: string;
  readonly readOnly: boolean;
  readonly clock: PlaybackClock;
  readonly cameraStreams: readonly StreamDescriptor[];
  /** Optional URDF scene driven by the same immutable playback clock. */
  readonly robotScene?: RobotSceneCoreProps & {
    readonly title?: string;
    readonly canonicalPath?: string;
  };
  /** Keeps a stable robot-pose workspace when the runtime binding is unavailable. */
  readonly robotSceneUnavailableReason?: string;
  readonly collectionItems: readonly WorkbenchCollectionItem[];
  readonly selectedCollectionItemId?: string;
  readonly onSelectCollectionItem?: (id: string) => void;
  readonly findings: readonly WorkbenchFinding[];
  readonly timelineTracks: readonly ViewerTimelineTrack[];
  readonly timelineSelection?: ViewerTimelineSelection;
  readonly timelineDisabled?: boolean;
  readonly timelineLabel?: string;
  readonly notes?: WorkbenchDiagnosticNotes;
  readonly actions: readonly WorkbenchAction[];
  readonly banner?: {
    readonly label: string;
    readonly title: string;
    readonly description: string;
    readonly tone: "warning" | "error" | "info";
  };
  readonly onTimeRangeSelect?: (startNs: string, endNs: string) => void;
  /** A fresh drag creates an interval instead of changing the selected Tag. */
  readonly onTimeRangeCreate?: (startNs: string, endNs: string) => void;
  readonly onResourceError?: (
    error: DomainError,
    scope: "video" | "curve" | "pointcloud" | "scene3d",
  ) => void;
}

export interface DataVisualizationWorkbenchSlotContext {
  readonly adapter: DataVisualizationWorkbenchAdapter;
}

export interface DataVisualizationWorkbenchSlots {
  /** Replace the left object rail while preserving the shared 18/56/26 shell. */
  readonly navigation?: (
    context: DataVisualizationWorkbenchSlotContext,
  ) => ReactNode;
  /** Adds page-level mode navigation and contextual actions below the workbench title. */
  readonly workspaceToolbar?: (
    context: DataVisualizationWorkbenchSlotContext,
  ) => ReactNode;
  /** Adds mode-specific controls above the Manifest camera surface. */
  readonly mediaHeader?: (
    context: DataVisualizationWorkbenchSlotContext,
  ) => ReactNode;
  /** Adds compact, time-range-aware controls between playback and the shared tracks. */
  readonly timelineTools?: (
    context: DataVisualizationWorkbenchSlotContext,
  ) => ReactNode;
  /** Replace quality findings with Tag tools/review without copying the viewer. */
  readonly inspector?: (
    context: DataVisualizationWorkbenchSlotContext,
  ) => ReactNode;
  /** Replace the lower-right command dock; caller remains responsible for capability gates. */
  readonly actionDock?: (
    context: DataVisualizationWorkbenchSlotContext,
  ) => ReactNode;
  /** Render overlays or deterministic test surfaces while retaining one clock and grid. */
  readonly renderPanel?: ViewerPanelRenderer;
}
