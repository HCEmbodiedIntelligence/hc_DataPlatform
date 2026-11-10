// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RobotSceneCore, type RobotSceneCoreProps, type RobotSceneRuntimeLoader } from "./RobotSceneCore";
import { createPlaybackClock } from "./PlaybackClock";

afterEach(cleanup);

function fixture() {
  const runtimes: { canvas: HTMLCanvasElement; applyTime: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> }[] = [];
  const inputs: RobotSceneCoreProps[] = [];
  const loader = vi.fn<RobotSceneRuntimeLoader>(async (_host, props) => {
    inputs.push(props);
    const runtime = { canvas: document.createElement("canvas"), applyTime: vi.fn(), dispose: vi.fn(), restoreContext: async () => true };
    runtimes.push(runtime);
    return { manifest: { ...props.modelRef, requiredJoints: ["q1"] }, runtime };
  });
  const props: RobotSceneCoreProps = {
    reuseKey: "authorized-scope-and-assets",
    modelRef: { modelId: "robot", modelVersion: "v1" }, jointMapping: { q1: "hinge" },
    clock: createPlaybackClock({ startNs: "0", endNs: "10000000000" }),
    jointFrameSource: { sampleAt: () => ({ q1: 1 }) }, runtimeLoader: loader,
  };
  return { props, loader, inputs, runtimes };
}

it("retains the authorized model canvas while rebinding the next task's clock and pose source", async () => {
  const { props, loader, inputs, runtimes } = fixture();
  const { rerender } = render(<RobotSceneCore {...props} />);
  await waitFor(() => expect(screen.getByLabelText("机器人 3D 场景")).toHaveAttribute("data-status", "ready"));
  const original = runtimes[0]!;
  const nextClock = createPlaybackClock({ startNs: "0", endNs: "20000000000" });
  rerender(<RobotSceneCore {...props} modelRef={{ ...props.modelRef }} jointMapping={{ ...props.jointMapping }}
    clock={nextClock} jointFrameSource={{ sampleAt: () => ({ q1: 2 }) }} runtimeLoader={vi.fn()} />);
  expect(loader).toHaveBeenCalledOnce();
  expect(screen.getByLabelText("机器人 3D 场景").querySelector("canvas")).toBe(original.canvas);
  expect(original.dispose).not.toHaveBeenCalled();
  expect(await inputs[0]!.jointFrameSource!.sampleAt("0", new AbortController().signal)).toEqual({ q1: 2 });
  original.applyTime.mockClear();
  act(() => props.clock!.seek("123"));
  expect(original.applyTime).not.toHaveBeenCalled();
  act(() => nextClock.seek("456"));
  expect(original.applyTime).toHaveBeenLastCalledWith("456");
});

it.each(["authorization", "model", "mapping"])("recreates the scene when its %s binding changes", async change => {
  const { props, loader, runtimes } = fixture();
  const { rerender } = render(<RobotSceneCore {...props} />);
  await waitFor(() => expect(runtimes).toHaveLength(1));
  rerender(<RobotSceneCore {...props}
    reuseKey={change === "authorization" ? "another-scope-and-assets" : props.reuseKey}
    modelRef={change === "model" ? { ...props.modelRef, modelVersion: "v2" } : props.modelRef}
    jointMapping={change === "mapping" ? { q1: "other-hinge" } : props.jointMapping} />);
  await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
  expect(runtimes[0]!.dispose).toHaveBeenCalledOnce();
  await waitFor(() => expect(screen.getByLabelText("机器人 3D 场景").querySelector("canvas")).toBe(runtimes[1]!.canvas));
});

it("ignores a late pose sample from the previous task", async () => {
  const { props, inputs } = fixture();
  let finish!: (frame: Record<string, number>) => void;
  const pending = new Promise<Record<string, number>>(resolve => { finish = resolve; });
  const { rerender } = render(<RobotSceneCore {...props} jointFrameSource={{ sampleAt: () => pending }} />);
  await waitFor(() => expect(inputs).toHaveLength(1));
  const sample = inputs[0]!.jointFrameSource!.sampleAt("0", new AbortController().signal);
  const rejected = expect(sample).rejects.toMatchObject({ name: "AbortError" });
  rerender(<RobotSceneCore {...props} jointFrameSource={{ sampleAt: () => ({ q1: 2 }) }} />);
  finish({ q1: 1 });
  await rejected;
});
