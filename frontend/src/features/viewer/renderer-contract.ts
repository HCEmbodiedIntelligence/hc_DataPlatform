import type { ComponentType } from "react";
import type { PlaybackClock } from "./PlaybackClock";
import type {
  StreamDescriptor,
  ViewerBuiltinPanelKind,
  ViewerPanelSpec,
  ViewerResourceScope,
} from "./types";

export interface ViewerRendererRef {
  readonly id: string;
  readonly version: string;
}

export type ViewerInputCapability = "media" | "window";

export interface ViewerRendererDescriptor extends ViewerRendererRef {
  readonly modalities: readonly string[];
  /** Existing generic renderers explicitly accept any schema. Extensions can bind exact versions. */
  readonly schemas:
    | "*"
    | readonly {
        readonly id: string;
        readonly versions: readonly string[] | "*";
      }[];
  readonly requiredInputs: readonly ViewerInputCapability[];
  /** Lower values win selection and appear first in the grid. Equal matches are rejected. */
  readonly priority: number;
  readonly minWidthPx: number;
  readonly aspectRatio?: string;
  readonly resourceScope: ViewerResourceScope;
  readonly jointAxes?: boolean;
}

export interface ViewerRendererContext {
  readonly panel: ViewerPanelSpec;
  readonly stream: StreamDescriptor;
  readonly clock: PlaybackClock;
  /** Aborted on hide, owner replacement, retry or unmount. */
  readonly signal: AbortSignal;
  /** Resources added after cancellation are immediately disposed too. */
  readonly resources: ViewerRendererResources;
  /** Async source failures use the same panel-local error and cleanup path as loader failures. */
  readonly reportError: (cause: unknown) => void;
}

/** Renderer resource port; the host owns disposal and can supply another implementation. */
export interface ViewerRendererResources {
  add(disposer: (() => void) | { dispose(): void } | null | undefined): void;
  trackObjectUrl(url: string): string;
  trackMedia(element: HTMLMediaElement): void;
  trackWorker(worker: Worker): void;
  trackAnimationFrame(id: number): void;
  trackGpuResource(resource: { dispose?: () => void }): void;
}

export interface ViewerRendererModule {
  readonly Component: ComponentType<ViewerRendererContext>;
}

export interface ViewerRendererRegistration {
  readonly descriptor: ViewerRendererDescriptor;
  readonly default: boolean;
  readonly implementation:
    | { readonly kind: "builtin"; readonly panelKind: ViewerBuiltinPanelKind }
    | {
        readonly kind: "lazy";
        readonly load: (signal: AbortSignal) => Promise<ViewerRendererModule>;
      };
}
