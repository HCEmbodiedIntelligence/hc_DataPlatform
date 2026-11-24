// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { cancelExport, clearExportTask, pauseExport, resumeExport, runExportTask, useExportTasks } from "./export-tasks";
import { writeExportFile } from "./save-file";

const scope = { organizationId: "org", projectId: "project", regionCode: "region" };
afterEach(() => { act(() => clearExportTask()); });

it.each(["resume", "cancel"] as const)("isolates a paused file and handles %s without publishing a partial ZIP", async action => {
  const { result } = renderHook(() => useExportTasks(scope));
  const file = { write: vi.fn(), close: vi.fn(), abort: vi.fn() };
  const handle = { name: "first.zip", createWritable: async () => file };
  let sendNext!: () => void;
  const next = new Promise<void>(resolve => { sendNext = resolve; });
  let first!: Promise<void>;
  act(() => {
    first = runExportTask(scope, "first", "zip", async ({ checkpoint, signal, saving }) => {
      await writeExportFile(handle, signal, async writer => {
        await writer.write(Uint8Array.of(1));
        await next;
        await checkpoint();
        await writer.write(Uint8Array.of(2));
      }, async () => { await checkpoint(); saving(); });
      return { kind: "saved", message: "first complete" };
    });
  });
  await waitFor(() => expect(file.write).toHaveBeenCalledTimes(1));
  const id = result.current[0]!.id;
  await act(async () => { pauseExport(id); sendNext(); });
  expect(result.current[0]?.status).toBe("paused");
  await act(async () => {
    await runExportTask(scope, "second", "oss", async () => ({ kind: "oss", uri: "oss://managed/second.zip" }));
  });
  expect(result.current.map(t => t.status)).toEqual(["finished", "paused"]);
  expect(file.write).toHaveBeenCalledTimes(1);
  await act(async () => {
    if (action === "resume") resumeExport(id);
    else cancelExport(id);
    await first;
  });
  expect(result.current.map(t => t.status)).toEqual(["finished", action === "resume" ? "finished" : "cancelled"]);
  expect(file.write).toHaveBeenCalledTimes(action === "resume" ? 2 : 1);
  expect(file.close).toHaveBeenCalledTimes(action === "resume" ? 1 : 0);
  expect(file.abort).toHaveBeenCalledTimes(action === "cancel" ? 1 : 0);
});
