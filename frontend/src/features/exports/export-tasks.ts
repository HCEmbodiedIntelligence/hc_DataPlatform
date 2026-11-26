import { create } from "zustand";
import { sameScope, type Scope } from "../../shared/scope/model";
import { hasSessionScopeAccess } from "../../shared/scope/session-access";
import { useShellStore } from "../../shared/scope/shell-store";
import type { ExportDestination } from "./oss-export";

type TaskStatus = "running" | "pausing" | "paused" | "saving" | "finished" | "failed" | "cancelled";
type Outcome = { kind: "saved"; message: string } | { kind: "oss"; uri: string } | { kind: "cancelled" };

export interface ExportControl {
  signal: AbortSignal;
  report: (message: string, completed?: number, total?: number) => void;
  checkpoint: () => Promise<void>;
  saving: () => void;
}

interface ExportTask {
  id: number;
  scope: Scope;
  label: string;
  destination: ExportDestination;
  status: TaskStatus;
  message: string;
  progress?: number;
  uri?: string;
  hiddenReceipt?: boolean;
}

interface TaskControl {
  controller: AbortController;
  resume?: () => void;
  gate?: Promise<void>;
}

const store = create<{ tasks: ExportTask[] }>(() => ({ tasks: [] }));
const controls = new Map<number, TaskControl>();
let nextId = 0;

export function useExportTasks(scope: Scope | null) {
  const tasks = store(state => state.tasks);
  return tasks.filter(task => scope && sameScope(scope, task.scope));
}

function update(id: number, change: Partial<ExportTask>) {
  store.setState(({ tasks }) => ({ tasks: tasks.map(t => t.id === id ? { ...t, ...change } : t) }));
}

export function pauseExport(id: number) {
  const control = controls.get(id);
  const task = store.getState().tasks.find(t => t.id === id);
  if (!control || task?.destination !== "zip" || task.status !== "running") return;
  control.gate = new Promise(resolve => { control.resume = resolve; });
  update(id, { status: "pausing" });
}

export function resumeExport(id: number) {
  const control = controls.get(id);
  if (!control?.gate || control.controller.signal.aborted) return;
  control.resume?.();
  control.gate = undefined;
  control.resume = undefined;
  update(id, { status: "running" });
}

export function cancelExport(id: number) {
  const task = store.getState().tasks.find(t => t.id === id);
  if (!task || !["running", "pausing", "paused"].includes(task.status)) return;
  const control = controls.get(id);
  control?.controller.abort();
  control?.resume?.();
  update(id, { status: "cancelled", message: task.destination === "zip" ? "下载已取消" : "已取消本次 OSS 导出请求" });
}

export function clearExportTask(id?: number) {
  for (const [key, control] of controls) {
    if (id !== undefined && key !== id) continue;
    control.controller.abort();
    control.resume?.();
  }
  store.setState(({ tasks }) => ({ tasks: id === undefined ? [] : tasks.filter(t => t.id !== id) }));
}

useShellStore.subscribe((state, previous) => {
  if (state.sessionRevision !== previous.sessionRevision || state.scopeKey !== previous.scopeKey || state.scopeChanging) {
    clearExportTask();
  } else if (state.sessionScopes !== previous.sessionScopes) {
    for (const task of store.getState().tasks)
      if (!hasSessionScopeAccess(state, task.scope)) clearExportTask(task.id);
  }
});

export async function runExportTask(scope: Scope, label: string, destination: ExportDestination,
  run: (control: ExportControl) => Promise<Outcome>) {
  const id = ++nextId;
  const control: TaskControl = { controller: new AbortController() };
  const { signal } = control.controller;
  controls.set(id, control);
  store.setState(({ tasks }) => ({
    tasks: [{ id, scope: { ...scope }, label, destination, status: "running", message: "正在准备导出…" },
      ...tasks.map(t => sameScope(t.scope, scope) ? { ...t, hiddenReceipt: true } : t)],
  }));
  async function checkpoint() {
    signal.throwIfAborted();
    while (control.gate) {
      update(id, { status: "paused" });
      await control.gate;
      signal.throwIfAborted();
    }
  }
  try {
    const result = await run({
      signal, checkpoint,
      saving: () => update(id, { status: "saving", message: "正在保存文件…" }),
      report: (message, completed, total) => {
        if (!signal.aborted) update(id, { message, progress: completed !== undefined && total && total > 0
          ? Math.min(100, Math.floor(100 * completed / total)) : undefined });
      },
    });
    if (signal.aborted) return;
    if (result.kind === "oss") update(id, { status: "finished", uri: result.uri, message: "已导出到 OSS" });
    else if (result.kind === "saved") update(id, { status: "finished", message: result.message });
    else update(id, { status: "cancelled", message: "尚未选择保存位置。可再次点击“导出”选择保存位置。" });
  } catch (error) {
    if (!signal.aborted) update(id, { status: "failed", message: error instanceof Error ? error.message : "导出失败" });
  } finally {
    control.resume?.();
    control.gate = undefined;
    controls.delete(id);
  }
}
