import { useEffect, useMemo, useReducer } from "react";
import { z } from "zod";
import { makeScopeKey, type Scope } from "../../shared/scope/model";
import { useShellStore } from "../../shared/scope/shell-store";

import type { PackageData } from "./package-deliveries";

export function episodeItem(data: PackageData): ExportItem {
  return {
    id: data.identity.episode_id,
    datasetId: data.identity.dataset_id,
    version: data.version,
    frames: data.frame_count,
  };
}

const itemSchema = z.object({
  id: z.string(),
  datasetId: z.string(),
  version: z.number().optional(),
  frames: z.number().optional(),
  episodes: z.number().optional(),
  files: z.number().optional(),
  taskId: z.string().nullish(),
  batches: z.array(z.number()).optional(),
  sourceFormat: z.string().optional(),
  annotationRevision: z.number().optional(),
});
export type ExportItem = z.infer<typeof itemSchema>;

const recordSchema = z.object({
  id: z.string(),
  uri: z.string().startsWith("oss://").optional(),
  filename: z.string().optional(),
  format: z.string().optional(),
  items: z.array(itemSchema).optional(),
  selectedBatches: z.array(z.number()).optional(),
  created: z.number(),
  source: z.enum(["raw", "bundle", "job"]),
  jobId: z.string().optional(),
});
export type ExportRecord = z.infer<typeof recordSchema>;
const savedEvent = "hc-export-history-saved";
const unsaved = new Map<string, ExportRecord>();

function keyPrefix(actorId: string, scope: Scope) {
  return `hc-export-history/${encodeURIComponent(actorId)}/${makeScopeKey(scope)}/`;
}

export function saveExportRecord(
  scope: Scope,
  result: Omit<ExportRecord, "created" | "id">,
) {
  const actorId = useShellStore.getState().principal?.actorId;
  if (!actorId) return;
  const id =
    result.uri ??
    Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-");
  const record = { ...result, id, created: Date.now() };
  const key = keyPrefix(actorId, scope) + encodeURIComponent(id);
  try {
    window.localStorage.setItem(key, JSON.stringify(record));
  } catch {
    // Keep the address available in this page when browser storage is full or disabled.
    unsaved.set(key, record);
  }
  window.dispatchEvent(new Event(savedEvent));
}

function readRecords(prefix: string) {
  const records = new Map<string, ExportRecord>();
  try {
    for (const key of Object.keys(window.localStorage)) {
      if (!key.startsWith(prefix)) continue;
      try {
        const record = recordSchema.parse(
          JSON.parse(window.localStorage.getItem(key)!),
        );
        records.set(key, record);
      } catch {
        // A stale or damaged entry should not hide the rest of the history.
      }
    }
  } catch {
    // Browser storage is unavailable.
  }
  for (const [key, record] of unsaved) {
    if (key.startsWith(prefix)) records.set(key, record);
  }
  return [...records.values()].sort((a, b) => b.created - a.created);
}

export function useExportHistory(scope: Scope | null) {
  const actorId = useShellStore((state) => state.principal?.actorId);
  const prefix = actorId && scope && keyPrefix(actorId, scope);
  const [revision, refresh] = useReducer((value: number) => value + 1, 0);
  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (prefix && (!event.key || event.key.startsWith(prefix))) refresh();
    }
    window.addEventListener(savedEvent, refresh);
    window.addEventListener("storage", onStorage);
    refresh();
    return () => {
      window.removeEventListener(savedEvent, refresh);
      window.removeEventListener("storage", onStorage);
    };
  }, [prefix]);
  return useMemo(() => (prefix ? readRecords(prefix) : []), [prefix, revision]);
}
