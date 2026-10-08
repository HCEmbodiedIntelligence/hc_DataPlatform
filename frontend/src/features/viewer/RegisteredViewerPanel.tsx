import { Component, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  createDomainError,
  isDomainError,
} from "../../shared/api/domain-error";
import type { PlaybackClock } from "./PlaybackClock";
import type {
  ViewerRendererContext,
  ViewerRendererModule,
  ViewerRendererRegistration,
} from "./renderer-contract";
import { ViewerResourceRegistry } from "./runtime/ViewerResourceRegistry";
import { usePanelVisibility } from "./runtime/use-panel-visibility";
import type {
  DomainError,
  StreamDescriptor,
  ViewerPanelSpec,
  ViewerResourceScope,
} from "./types";

type ResourceErrorHandler = (
  error: DomainError,
  scope: ViewerResourceScope,
) => void;

class PanelBoundary extends Component<
  {
    readonly children: ReactNode;
    readonly onError: (error: unknown) => void;
  },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    this.props.onError(error);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

export function RegisteredViewerPanel({
  panel,
  stream,
  clock,
  registration,
  onResourceError,
}: {
  readonly panel: ViewerPanelSpec;
  readonly stream: StreamDescriptor;
  readonly clock: PlaybackClock;
  readonly registration: ViewerRendererRegistration;
  readonly onResourceError?: ResourceErrorHandler;
}) {
  const host = useRef<HTMLElement>(null);
  const visible = usePanelVisibility(host);
  const owner = useMemo(
    () => ({ panel, stream, clock, registration }),
    [panel, stream, clock, registration],
  );
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{
    readonly owner: typeof owner;
    readonly context?: ViewerRendererContext;
    readonly module?: ViewerRendererModule;
    readonly error?: DomainError;
    readonly fail: (error: unknown) => void;
  }>();
  const onError = useRef(onResourceError);
  useEffect(() => {
    onError.current = onResourceError;
  }, [onResourceError]);

  useEffect(() => {
    if (!visible || panel.state === "unsupported" || panel.state === "missing")
      return;
    const implementation = registration.implementation;
    if (implementation.kind !== "lazy") return;
    const controller = new AbortController();
    const resources = new ViewerResourceRegistry();
    const fail = (cause: unknown) => {
      if (controller.signal.aborted) return;
      const error = isDomainError(cause)
        ? cause
        : createDomainError({
            code: "SERVER_ERROR",
            message:
              cause instanceof Error ? cause.message : "可视化资源加载失败",
            fieldErrors: [],
            operationErrors: [],
            blockedReasons: [],
            requestId: null,
            retryable: true,
            httpStatus: null,
          });
      controller.abort();
      resources.dispose();
      setLoaded({ owner, error, fail });
      onError.current?.(error, registration.descriptor.resourceScope);
    };
    setLoaded(undefined);
    const context = Object.freeze({
      panel,
      stream,
      clock,
      signal: controller.signal,
      resources,
      reportError: fail,
    });
    Promise.resolve()
      .then(() => {
        if (controller.signal.aborted) return undefined;
        return implementation.load(controller.signal);
      })
      .then((module) => {
        if (controller.signal.aborted) return;
        if (!module?.Component) throw new Error("可视化能力未提供组件");
        setLoaded({ owner, context, module, fail });
      })
      .catch(fail);
    return () => {
      controller.abort();
      resources.dispose();
    };
  }, [owner, visible, attempt, panel, stream, clock, registration]);

  const current = loaded?.owner === owner && visible ? loaded : undefined;
  const Renderer = current?.module?.Component;
  const unavailable =
    panel.state === "unsupported" || panel.state === "missing";
  return (
    <article
      ref={host}
      className="viewer-panel"
      data-panel-kind={panel.kind}
      data-panel-state={panel.state}
      data-panel-visible={visible || undefined}
      data-renderer-version={registration.descriptor.version}
      aria-labelledby={`${panel.panelId}-title`}
    >
      <header>
        <h3 id={`${panel.panelId}-title`}>{panel.title}</h3>
      </header>
      {unavailable ? <div role="note">此 Stream 当前不可用。</div> : null}
      {current?.error ? (
        <div role="alert">
          资源加载失败：{current.error.message}。其他面板仍可使用。
          {current.error.retryable ? (
            <button
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
            >
              重试此面板
            </button>
          ) : null}
        </div>
      ) : null}
      {Renderer && current?.context ? (
        <PanelBoundary key={attempt} onError={current.fail}>
          <Renderer {...current.context} />
        </PanelBoundary>
      ) : !unavailable && !current?.error ? (
        <div role="status">可视化资源加载中</div>
      ) : null}
    </article>
  );
}
