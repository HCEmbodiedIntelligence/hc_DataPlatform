export type ViewerStreamModality =
  | "rgb"
  | "depth"
  | "pointcloud"
  | "joint_state"
  | "action"
  | "force"
  | "pose"
  | "imu"
  | "tactile"
  | "event"
  | "other";

export type ViewerResourceScope = "video" | "curve" | "pointcloud" | "scene3d";

export type { DomainError } from "../../shared/api/domain-error";

export interface ViewerAxisDescriptor {
  readonly axisId: string;
  readonly sourceName: string;
  readonly displayName: string;
  readonly sampleIndex: number;
  readonly unit: string;
  readonly mappedJointName?: string;
  readonly mappingStatus: "mapped" | "unmapped" | "not-applicable";
  readonly limit?: { readonly min: number; readonly max: number };
}

export interface ViewerWindow {
  readonly startNs: string;
  readonly endNs: string;
  readonly lod: number;
}

export interface ViewerWindowPayload {
  readonly generation: number;
  readonly timestampsNs: readonly string[];
  readonly values?: readonly (readonly number[])[];
  /** Optional metadata for numeric vector dimensions such as robot joints. */
  readonly series?: readonly ViewerSeriesDescriptor[];
  /** Discrete facts for an EVENT stream, keyed to the same immutable time base. */
  readonly events?: readonly ViewerEventSample[];
  readonly points?: Float32Array;
  /** One point-cloud sample per source step; the renderer selects by clock time. */
  readonly pointFrames?: readonly ViewerPointFrame[];
  readonly dispose?: () => void;
}

export interface ViewerSeriesDescriptor {
  readonly id: string;
  readonly displayName: string;
  readonly unit?: string;
}

export interface ViewerEventSample {
  readonly timestampNs: string;
  readonly label: string;
}

export interface ViewerPointFrame {
  readonly timestampNs: string;
  readonly points: Float32Array;
}

export interface ViewerWindowSource {
  loadWindow(
    window: ViewerWindow,
    signal: AbortSignal,
  ): Promise<ViewerWindowPayload>;
}

export interface AuthorizedMediaDescriptor {
  readonly url: string;
  readonly expiresAt: string;
  readonly kind: "rgb-video" | "depth-preview";
  readonly revoke?: () => void;
}

export interface ViewerMediaSource {
  authorize(
    signal: AbortSignal,
    onStatus?: (status: "preparing" | "ready" | "failed") => void,
  ): Promise<AuthorizedMediaDescriptor>;
  refresh(
    signal: AbortSignal,
    onStatus?: (status: "preparing" | "ready" | "failed") => void,
  ): Promise<AuthorizedMediaDescriptor>;
}

export interface StreamDescriptor {
  readonly id: string;
  readonly channelDefinitionId?: string;
  readonly canonicalPath: string;
  readonly displayName: string;
  readonly modality: ViewerStreamModality | (string & {});
  readonly semanticRole?: string;
  readonly schema: {
    readonly id: string;
    readonly version: string;
    readonly dtype?: string;
    readonly shape?: readonly number[];
    readonly unit?: string;
    readonly encoding?: string;
    readonly axes?: readonly ViewerAxisDescriptor[];
  };
  readonly rateHz?: number;
  readonly startNs: string;
  readonly endNs: string;
  readonly frame?: { readonly id: string; readonly name: string };
  readonly clockDomain?: { readonly id: string; readonly name: string };
  readonly calibrationSetId?: string;
  readonly availability:
    | "ready"
    | "media-preparing"
    | "partial"
    | "unsupported"
    | "missing";
  /** A concise non-visual summary for Canvas/3D alternatives. */
  readonly accessibleSummary?: string;
  readonly mediaSource?: ViewerMediaSource;
  readonly windowSource?: ViewerWindowSource;
}

export interface OverlayMountContext {
  readonly container: HTMLElement;
  readonly clock: PlaybackClock;
  readonly signal: AbortSignal;
}

export interface OverlayRenderer {
  readonly id: string;
  readonly label: string;
  mount(
    context: OverlayMountContext,
  ): void | (() => void) | { dispose(): void };
}

export type ViewerPanelKind =
  | "video"
  | "depth"
  | "pointcloud-preview"
  | "time-series"
  | "event-track"
  | "unsupported";

export interface ViewerPanelSpec {
  readonly panelId: string;
  readonly kind: ViewerPanelKind;
  readonly streamIds: readonly string[];
  readonly title: string;
  readonly priority: number;
  readonly minWidthPx: number;
  readonly aspectRatio?: string;
  readonly state: "ready" | "pending" | "partial" | "unsupported" | "missing";
}

export interface ViewerCompositionDiagnostic {
  readonly code: string;
  readonly streamId: string;
  readonly message: string;
}

export interface ResolvedViewerComposition {
  readonly panels: readonly ViewerPanelSpec[];
  readonly jointGroups: ReadonlyArray<{
    readonly streamId: string;
    readonly title: string;
    readonly axes: readonly ViewerAxisDescriptor[];
  }>;
  readonly diagnostics: readonly ViewerCompositionDiagnostic[];
}
import type { PlaybackClock } from "./PlaybackClock";
