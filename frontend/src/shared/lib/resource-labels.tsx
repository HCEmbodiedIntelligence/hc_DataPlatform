import { z } from "zod";
import { request } from "../api/http-client";
import { parseWire } from "../api/validate";
import { useScopedQuery, useScopeQueryRevision } from "../api/use-scoped-query";
import { getShellState, useShellStore } from "../scope/shell-store";
import type { Scope } from "../scope/model";

export type ResourceKind =
  | "dataset"
  | "collection_task"
  | "robot"
  | "episode"
  | "annotation_task"
  | "rollout";
export type ResourceReference = {
  kind: ResourceKind;
  id: string;
  dataset_id?: string;
};
const labelSchema = z.object({
  kind: z.enum([
    "dataset",
    "collection_task",
    "robot",
    "episode",
    "annotation_task",
    "rollout",
  ]),
  id: z.string(),
  dataset_id: z.string().nullable(),
  label: z.string(),
  description_label: z.string().nullable(),
});
const responseSchema = z.object({ items: z.array(labelSchema) });
type Label = z.infer<typeof labelSchema>;
type Pending = {
  reference: ResourceReference;
  resolve: (value: Label | null) => void;
  reject: (error: unknown) => void;
};
const batches = new Map<string, Pending[]>();

function key(ref: ResourceReference): string {
  return JSON.stringify([ref.kind, ref.id, ref.dataset_id ?? null]);
}

/** One bounded lookup per render batch, shared by tables, selectors and detail headers. */
function loadLabel(
  scope: Scope,
  revision: string,
  reference: ResourceReference,
): Promise<Label | null> {
  const batchKey = JSON.stringify([scope, revision]);
  const sessionRevision = getShellState().sessionRevision;
  return new Promise((resolve, reject) => {
    const existing = batches.get(batchKey);
    if (existing) {
      existing.push({ reference, resolve, reject });
      return;
    }
    const pending = [{ reference, resolve, reject }];
    batches.set(batchKey, pending);
    setTimeout(() => {
      batches.delete(batchKey);
      const unique = [
        ...new Map(
          pending.map((item) => [key(item.reference), item.reference]),
        ).values(),
      ];
      void (async () => {
        const labels = new Map<string, Label>();
        for (let offset = 0; offset < unique.length; offset += 100) {
          if (getShellState().sessionRevision !== sessionRevision)
            throw new DOMException("Session changed", "AbortError");
          const endpoint = `/projects/${encodeURIComponent(scope.projectId!)}/datasets:display-labels`;
          const raw = await request<unknown>({
            method: "POST",
            path: endpoint,
            scopeMode: "explicit",
            scope,
            body: { references: unique.slice(offset, offset + 100) },
            cache: "no-store",
          });
          const response = parseWire(responseSchema, raw, { endpoint });
          for (const label of response.items)
            labels.set(
              key({
                kind: label.kind,
                id: label.id,
                ...(label.dataset_id ? { dataset_id: label.dataset_id } : {}),
              }),
              label,
            );
        }
        if (getShellState().sessionRevision !== sessionRevision)
          throw new DOMException("Session changed", "AbortError");
        for (const item of pending)
          item.resolve(labels.get(key(item.reference)) ?? null);
      })().catch((error: unknown) =>
        pending.forEach((item) => item.reject(error)),
      );
    }, 0);
  });
}

export function decodedFileName(value: string): string {
  let decoded = value;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    /* Preserve malformed legacy names. */
  }
  return (decoded.replaceAll("\\", "/").split("/").at(-1) ?? decoded)
    .replace(/[\p{Cc}\p{Cf}]/gu, "")
    .normalize("NFC");
}

export function taskNumber(value: string | null | undefined): string {
  return value && /^\d+$/u.test(value) ? value.replace(/^0+(?=\d)/u, "") : "—";
}

export function readableFallback(
  kind: ResourceKind,
  id: string,
  fallback?: string,
): string {
  if (fallback) return fallback;
  if (kind === "episode") {
    const match = /^episode_0*(\d+)$/iu.exec(id);
    if (match) return `Episode ${match[1]}`;
  }
  if (kind === "collection_task" && /^\d+$/u.test(id)) return taskNumber(id);
  // Preserve actual human labels from older APIs, but never expose a hash as a name.
  if (
    id &&
    !/[0-9a-f]{20,}|[0-9a-f]{8}-[0-9a-f-]{27}/iu.test(id) &&
    id.length <= 64
  )
    return decodedFileName(id);
  return {
    dataset: "数据集",
    collection_task: "—",
    robot: "未命名机器人",
    episode: "Episode",
    annotation_task: "标注任务",
    rollout: "录制",
  }[kind];
}

export function useResourceLabel(
  reference: ResourceReference,
  explicitScope?: Scope,
) {
  const selectedScope = useShellStore((state) => state.scope);
  const scope = explicitScope ?? selectedScope;
  const revision = useScopeQueryRevision();
  return useScopedQuery(scope, "resource-labels", reference.kind, reference, {
    enabled: Boolean(scope?.projectId && scope.regionCode && reference.id),
    queryFn: () => loadLabel(scope!, revision, reference),
    staleTime: 60_000,
    retry: false,
  });
}

export function useResourceNames(
  references: readonly ResourceReference[],
  explicitScope?: Scope,
) {
  const selectedScope = useShellStore((state) => state.scope);
  const scope = explicitScope ?? selectedScope;
  const revision = useScopeQueryRevision();
  return useScopedQuery(scope, "resource-labels", "names", references, {
    enabled: Boolean(scope?.projectId && scope.regionCode && references.length),
    queryFn: async () => {
      const labels = await Promise.all(
        references.map((ref) => loadLabel(scope!, revision, ref)),
      );
      return Object.fromEntries(
        labels.flatMap((label) => (label ? [[label.id, label.label]] : [])),
      ) as Readonly<Record<string, string>>;
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function ResourceLabel({
  kind,
  id,
  datasetId,
  fallback,
  scope,
}: {
  kind: ResourceKind;
  id: string | null | undefined;
  datasetId?: string;
  fallback?: string;
  scope?: Scope;
}) {
  const result = useResourceLabel(
    { kind, id: id ?? "", ...(datasetId ? { dataset_id: datasetId } : {}) },
    scope,
  );
  return (
    <span title={id ?? undefined}>
      {result.data?.label ?? (id ? readableFallback(kind, id, fallback) : "—")}
    </span>
  );
}

export function DatasetDescription({
  datasetId,
  description,
}: {
  datasetId: string;
  description: string;
}) {
  const result = useResourceLabel({ kind: "dataset", id: datasetId });
  if (!description) return null;
  return (
    <>
      <strong>{result.data?.description_label ?? "数据集说明"}：</strong>
      {description}
    </>
  );
}
