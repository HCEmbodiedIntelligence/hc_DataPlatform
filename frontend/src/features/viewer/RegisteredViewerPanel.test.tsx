// @vitest-environment jsdom
import { useEffect } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  EpisodeWorkbenchCore,
  ViewerMediaSurface,
} from "./EpisodeWorkbenchCore";
import { DataVisualizationWorkbench } from "./DataVisualizationWorkbench";
import { createPlaybackClock } from "./PlaybackClock";
import { builtinViewerRenderers } from "./builtin-renderers";
import { createViewerRendererCatalog } from "./renderer-catalog";
import type {
  ViewerRendererContext,
  ViewerRendererModule,
  ViewerRendererRegistration,
} from "./renderer-contract";
import type { StreamDescriptor, ViewerWindowSource } from "./types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function textStream(
  overrides: Partial<StreamDescriptor> = {},
): StreamDescriptor {
  return {
    id: "caption",
    canonicalPath: "/caption",
    displayName: "字幕",
    modality: "text",
    schema: { id: "test.caption.events", version: "1" },
    startNs: "0",
    endNs: "1000000000",
    availability: "ready",
    windowSource: {
      loadWindow: async () => ({
        generation: 1,
        timestampsNs: ["0"],
        events: [{ timestampNs: "0", label: "原始字幕" }],
      }),
    },
    ...overrides,
  };
}

function catalog(load: (signal: AbortSignal) => Promise<ViewerRendererModule>) {
  const registration: ViewerRendererRegistration = {
    descriptor: {
      id: "captions",
      version: "1",
      modalities: ["text"],
      schemas: [{ id: "test.caption.events", versions: ["1"] }],
      requiredInputs: ["window"],
      priority: 60,
      minWidthPx: 420,
      resourceScope: "curve",
    },
    default: true,
    implementation: { kind: "lazy", load },
  };
  return createViewerRendererCatalog([...builtinViewerRenderers, registration]);
}

function trackedModule(
  dispose: () => void,
  contexts: ViewerRendererContext[],
): ViewerRendererModule {
  return {
    Component: function Extension(context) {
      useEffect(() => {
        contexts.push(context);
        context.resources.add(dispose);
      }, [context.stream, context.signal, context.resources]);
      return <output aria-label="受控扩展">{context.stream.id}</output>;
    },
  };
}

describe("registered renderers in the unchanged workbench", () => {
  it("routes asynchronous source errors through the same cancellation and panel-local failure path", async () => {
    const source = vi
      .fn<ViewerWindowSource["loadWindow"]>()
      .mockRejectedValue(new Error("源读取失败"));
    const error = vi.fn();
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    render(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream({ windowSource: { loadWindow: source } })]}
        rendererCatalog={catalog(
          () => import("./testing/CaptionRendererFixture"),
        )}
        onResourceError={error}
      />,
    );
    await screen.findByText(/源读取失败/);
    expect(error).toHaveBeenCalledWith(
      expect.objectContaining({ message: "源读取失败" }),
      "curve",
    );
    expect(source.mock.calls[0]?.[1].aborted).toBe(true);
    clock.dispose();
  });

  it("releases a source payload that arrives after its renderer has unmounted", async () => {
    const dispose = vi.fn();
    let complete:
      | ((value: {
          generation: number;
          timestampsNs: string[];
          dispose: () => void;
        }) => void)
      | undefined;
    const source = vi.fn<ViewerWindowSource["loadWindow"]>(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    const view = render(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream({ windowSource: { loadWindow: source } })]}
        rendererCatalog={catalog(
          () => import("./testing/CaptionRendererFixture"),
        )}
      />,
    );
    await waitFor(() => expect(source).toHaveBeenCalledTimes(1));
    view.unmount();
    expect(source.mock.calls[0]?.[1].aborted).toBe(true);
    await act(async () => {
      complete?.({ generation: 1, timestampsNs: [], dispose });
    });
    expect(dispose).toHaveBeenCalledTimes(1);
    clock.dispose();
  });

  it("contains component initialization errors and releases already registered resources", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const dispose = vi.fn();
    const error = vi.fn();
    let signal: AbortSignal | undefined;
    const module: ViewerRendererModule = {
      Component: function Broken(context) {
        useEffect(() => {
          signal = context.signal;
          context.resources.add(dispose);
          throw new Error("扩展初始化失败");
        }, [context.signal, context.resources]);
        return <span>初始化扩展</span>;
      },
    };
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    render(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream()]}
        rendererCatalog={catalog(async () => module)}
        onResourceError={error}
      />,
    );
    await screen.findByText(/扩展初始化失败/);
    expect(error).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(true);
    expect(dispose).toHaveBeenCalledTimes(1);
    clock.dispose();
  });

  it("lazy loads a real extension module, without a robot, sharing source and playback clock", async () => {
    const dispose = vi.fn();
    const source = vi.fn<ViewerWindowSource["loadWindow"]>(async () => ({
      generation: 1,
      timestampsNs: ["0"],
      events: [{ timestampNs: "0", label: "真实源字幕" }],
      dispose,
    }));
    const stream = textStream({ windowSource: { loadWindow: source } });
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    const load = vi.fn(() => import("./testing/CaptionRendererFixture"));
    const view = render(
      <EpisodeWorkbenchCore
        episodeId="episode"
        datasetId="dataset"
        versionId="version"
        mode="readonly"
        clock={clock}
        streams={[stream]}
        rendererCatalog={catalog(load)}
      />,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("扩展字幕")).toHaveTextContent(
        "真实源字幕 @ 0",
      ),
    );
    act(() => clock.seek("1000"));
    await waitFor(() =>
      expect(screen.getByLabelText("扩展字幕")).toHaveTextContent("@ 1000"),
    );
    expect(load).toHaveBeenCalledTimes(1);
    const signal = source.mock.calls[0]?.[1];
    expect(signal?.aborted).toBe(false);
    view.unmount();
    expect(signal?.aborted).toBe(true);
    expect(dispose).toHaveBeenCalledTimes(1);
    clock.dispose();
  });

  it("passes the same catalog through the reusable data visualization shell and keeps slots", async () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    render(
      <DataVisualizationWorkbench
        adapter={{
          mode: "raw-diagnostic",
          id: "data",
          title: "可复用工作台",
          readOnly: true,
          clock,
          cameraStreams: [textStream()],
          collectionItems: [],
          findings: [],
          timelineTracks: [],
          actions: [],
        }}
        slots={{
          rendererCatalog: catalog(
            () => import("./testing/CaptionRendererFixture"),
          ),
          inspector: () => <span>保留审核插槽</span>,
        }}
      />,
    );
    await screen.findByLabelText("扩展字幕");
    expect(screen.getByText("保留审核插槽")).toBeInTheDocument();
    clock.dispose();
  });

  it("cancels a pending loader on owner replacement and never mounts its late result", async () => {
    let complete: ((module: ViewerRendererModule) => void) | undefined;
    const signals: AbortSignal[] = [];
    const lateComponent = vi.fn(() => <span>旧扩展</span>);
    const load = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<ViewerRendererModule>((resolve) => {
        complete = resolve;
      });
    });
    const rendererCatalog = catalog(load);
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    const view = render(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream()]}
        rendererCatalog={rendererCatalog}
      />,
    );
    await waitFor(() => expect(signals).toHaveLength(1));
    const firstComplete = complete;
    view.rerender(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream({ id: "replacement" })]}
        rendererCatalog={rendererCatalog}
      />,
    );
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0]?.aborted).toBe(true);
    await act(async () => {
      firstComplete?.({ Component: lateComponent });
    });
    expect(lateComponent).not.toHaveBeenCalled();
    expect(screen.queryByText("旧扩展")).not.toBeInTheDocument();
    view.unmount();
    expect(signals[1]?.aborted).toBe(true);
    clock.dispose();
  });

  it("disposes old resources on stream replacement, and immediately disposes late registrations", async () => {
    const contexts: ViewerRendererContext[] = [];
    const dispose = vi.fn();
    const module = trackedModule(dispose, contexts);
    const rendererCatalog = catalog(async () => module);
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    const view = render(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream()]}
        rendererCatalog={rendererCatalog}
      />,
    );
    await screen.findByLabelText("受控扩展");
    const previous = contexts[0]!;
    view.rerender(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream({ id: "replacement" })]}
        rendererCatalog={rendererCatalog}
      />,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("受控扩展")).toHaveTextContent(
        "replacement",
      ),
    );
    expect(previous.signal.aborted).toBe(true);
    expect(dispose).toHaveBeenCalledTimes(1);
    const late = vi.fn();
    previous.resources.add(late);
    expect(late).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(dispose).toHaveBeenCalledTimes(2);
    clock.dispose();
  });

  it("does not load unknown schemas or unavailable streams", async () => {
    const load = vi.fn(() => import("./testing/CaptionRendererFixture"));
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    render(
      <ViewerMediaSurface
        clock={clock}
        streams={[
          textStream({ schema: { id: "test.caption.events", version: "2" } }),
          textStream({ id: "missing", availability: "missing" }),
        ]}
        rendererCatalog={catalog(load)}
      />,
    );
    await act(async () => undefined);
    expect(load).not.toHaveBeenCalled();
    expect(screen.getByText("此 Stream 当前不支持。")).toBeInTheDocument();
    clock.dispose();
  });

  it("limits loader errors to one panel and retries through the same frozen registration", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new Error("模块下载失败"))
      .mockImplementation(() => import("./testing/CaptionRendererFixture"));
    const error = vi.fn();
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    render(
      <ViewerMediaSurface
        clock={clock}
        streams={[
          textStream(),
          {
            ...textStream({ id: "camera", displayName: "独立视频" }),
            modality: "rgb",
          },
        ]}
        rendererCatalog={catalog(load)}
        onResourceError={error}
      />,
    );
    await screen.findByText(/模块下载失败/);
    expect(
      screen.getByRole("heading", { name: "独立视频" }),
    ).toBeInTheDocument();
    expect(error).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重试此面板" }));
    await screen.findByLabelText("扩展字幕");
    expect(load).toHaveBeenCalledTimes(2);
    clock.dispose();
  });

  it("aborts and disposes an extension when its panel becomes invisible", async () => {
    let intersection: IntersectionObserverCallback | undefined;
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: IntersectionObserverCallback) {
          intersection = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    const contexts: ViewerRendererContext[] = [];
    const dispose = vi.fn();
    const load = vi.fn(async () => trackedModule(dispose, contexts));
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    render(
      <ViewerMediaSurface
        clock={clock}
        streams={[textStream()]}
        rendererCatalog={catalog(load)}
      />,
    );
    expect(load).not.toHaveBeenCalled();
    act(() =>
      intersection?.(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      ),
    );
    await screen.findByLabelText("受控扩展");
    const first = contexts[0]!;
    act(() =>
      intersection?.(
        [{ isIntersecting: false } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      ),
    );
    expect(first.signal.aborted).toBe(true);
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("受控扩展")).not.toBeInTheDocument();
    clock.dispose();
  });
});
