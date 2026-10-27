// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createPlaybackClock } from "./PlaybackClock";
import { SynchronizedEpisodeWorkbench } from "./SynchronizedEpisodeWorkbench";
import { DataVisualizationWorkbench } from "./DataVisualizationWorkbench";
import type { StreamDescriptor, ViewerWindowPayload } from "./types";
import type { DataVisualizationWorkbenchAdapter } from "./workbench-contract";

afterEach(cleanup);
const origin = "1700000000000000000";
const end = "1700000001000000000";
function stream(
  id: string,
  modality: string,
  loadWindow: NonNullable<StreamDescriptor["windowSource"]>["loadWindow"],
): StreamDescriptor {
  return {
    id,
    modality,
    displayName: `/${id}`,
    canonicalPath: `/${id}`,
    schema: { id, version: "1" },
    startNs: origin,
    endNs: end,
    availability: "ready",
    windowSource: { loadWindow },
  };
}
function adapter(
  clock: ReturnType<typeof createPlaybackClock>,
  id = "episode-a",
): DataVisualizationWorkbenchAdapter {
  return {
    id,
    title: id,
    mode: "published-readonly",
    readOnly: true,
    clock,
    cameraStreams: [],
    collectionItems: [],
    findings: [],
    actions: [],
    timelineTracks: [],
  };
}
it.each(["synchronized", "media"] as const)("drives the %s robot with the workbench clock when the scene has no clock", async (layout) => {
  const clock = createPlaybackClock({ startNs: origin, endNs: end });
  const applyTime = vi.fn();
  const dispose = vi.fn();
  const runtimeLoader = vi.fn(async () => ({
    manifest: { modelId: "robot", modelVersion: "v1", requiredJoints: [] },
    runtime: {
      canvas: document.createElement("canvas"),
      applyTime,
      dispose,
      restoreContext: async () => true,
    },
  }));
  const model = {
    ...adapter(clock),
    robotScene: {
      modelRef: { modelId: "robot", modelVersion: "v1" },
      jointMapping: {},
      runtimeLoader,
    },
  };
  const content = () => layout === "synchronized"
    ? <SynchronizedEpisodeWorkbench adapter={model} jointAngleStream={null} />
    : <DataVisualizationWorkbench adapter={model} />;
  const ui = render(content());
  await waitFor(() => expect(applyTime).toHaveBeenLastCalledWith(origin));
  act(() => clock.seek("1700000000500000000"));
  expect(applyTime).toHaveBeenLastCalledWith("1700000000500000000");
  ui.rerender(content());
  expect(runtimeLoader).toHaveBeenCalledTimes(1);
  act(() => { clock.play(); clock.pause(); });
  expect(applyTime).toHaveBeenCalledTimes(4);
  ui.unmount();
  act(() => clock.seek(end));
  expect(applyTime).toHaveBeenCalledTimes(4);
  expect(dispose).toHaveBeenCalledTimes(1);
  clock.dispose();
});
it("switches to actual action windows, cancels the previous source and keeps values synchronized without assuming angular units", async () => {
  const clock = createPlaybackClock({ startNs: origin, endNs: end });
  let resolveJoint!: (payload: ViewerWindowPayload) => void;
  let jointSignal!: AbortSignal;
  const jointLoad = vi.fn((_window, signal: AbortSignal) => {
    jointSignal = signal;
    return new Promise<ViewerWindowPayload>((resolve) => {
      resolveJoint = resolve;
    });
  });
  const actionLoad = vi.fn(async () => ({
    generation: 0,
    timestampsNs: [origin, end],
    values: [
      [0.1, 0.2],
      [0.9, 0.8],
    ],
    series: [
      { id: "a", displayName: "A" },
      { id: "b", displayName: "B" },
    ],
  }));
  const joint = stream("joint", "joint_state", jointLoad);
  const action = stream("action", "action", actionLoad);
  const eventLoad = vi.fn();
  const event = stream("event", "event", eventLoad);
  const ui = render(
    <SynchronizedEpisodeWorkbench
      adapter={adapter(clock)}
      jointAngleStream={joint}
      numericStreams={[joint, action, event]}
    />,
  );
  await waitFor(() => expect(jointLoad).toHaveBeenCalledTimes(1));
  expect(actionLoad).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("option", { name: "/event" }),
  ).not.toBeInTheDocument();
  expect(eventLoad).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("数值通道"), {
    target: { value: "action" },
  });
  expect(await screen.findByRole("article", { name: "动作指令变化" })).toBeVisible();
  await waitFor(() => expect(actionLoad).toHaveBeenCalledTimes(1));
  expect(jointSignal.aborted).toBe(true);
  expect(await screen.findByText("0.10")).toBeVisible();
  expect(screen.getByText("单位未声明")).toBeVisible();
  expect(screen.queryByText(/rad/)).not.toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: /数值时间序列，共 2 个维度/ }),
  ).toBeVisible();
  expect(
    screen.queryByRole("img", { name: /关节|弧度/ }),
  ).not.toBeInTheDocument();
  act(() => clock.seek(end));
  expect(await screen.findByText("0.90")).toBeVisible();
  await act(async () =>
    resolveJoint({ generation: 0, timestampsNs: [origin], values: [[999]] }),
  );
  expect(screen.queryByText(/999/)).not.toBeInTheDocument();
  expect(screen.queryByText(/1700000000/)).not.toBeInTheDocument();
  ui.unmount();
  clock.dispose();
});
it("removes selected values when a fixed snapshot no longer exposes any numeric stream", async () => {
  const clock = createPlaybackClock({ startNs: origin, endNs: end });
  const action = stream("action", "action", async () => ({
    generation: 0,
    timestampsNs: [origin],
    values: [[42]],
  }));
  const ui = render(
    <SynchronizedEpisodeWorkbench
      adapter={adapter(clock)}
      jointAngleStream={null}
      numericStreams={[action]}
    />,
  );
  expect(await screen.findByText("42.00")).toBeVisible();
  ui.rerender(
    <SynchronizedEpisodeWorkbench
      adapter={adapter(clock, "episode-b")}
      jointAngleStream={null}
      numericStreams={[]}
    />,
  );
  expect(screen.queryByText("42.00")).not.toBeInTheDocument();
  expect(screen.queryByText("动作指令变化")).not.toBeInTheDocument();
  ui.unmount();
  clock.dispose();
});
