import { z } from "zod";
import { request } from "../../../shared/api/http-client";
import type { Scope } from "../../../shared/scope/model";
import { episodeDataReferenceWireSchema } from "../api/episode-package-wire";
import { sameAnnotationDataRef } from "../../annotation/runtime/annotation-package";
import { stepToTimelineNs } from "../../annotation/runtime/runtime-annotation-adapter";
import { buildTagTimelineTracks } from "../../annotation/runtime/tag-timeline";
import { episodeEndpoint, type EpisodeOverview } from "./episode-detail";

const step = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const revisionWire = z.object({
  task_id: z.string().min(1),
  revision: step,
  content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
  data_ref: episodeDataReferenceWireSchema,
  tag_schema_id: z.string().min(1),
  tag_schema_version: step.positive(),
  tags: z.array(
    z.object({
      annotation_id: z.string().min(1),
      tag_id: z.string().min(1),
      path: z.array(z.string()),
      label: z.string().nullable().optional(),
      parent_annotation_id: z.string().nullable().optional(),
      start_step: step,
      end_step: step.positive(),
    }),
  ),
});
const schemaWire = z.object({
  project_id: z.string(),
  schema_id: z.string(),
  version: step.positive(),
  document: z.object({
    nodes: z.array(z.object({ tag_id: z.string(), display_name: z.string() })),
  }),
});

/** Load the saved revision bound to this exact Episode, including unsubmitted Tags. */
export async function loadEpisodeAnnotationTracks(
  scope: Scope,
  overview: EpisodeOverview,
  startNs: string,
  endNs: string,
  signal?: AbortSignal,
) {
  episodeEndpoint(scope, overview.data);
  const selected = overview.annotation.current_revision;
  if (overview.annotation.availability !== "AVAILABLE" || !selected)
    throw new Error("当前 Episode 未选定已保存的标注。");
  const revision = revisionWire.parse(
    await request({
      method: "GET",
      path: `/annotation-tasks/${encodeURIComponent(selected.task_id)}/revisions/${selected.revision}`,
      scope,
      signal,
      cache: "no-store",
    }),
  );
  if (
    revision.task_id !== selected.task_id ||
    revision.task_id !== overview.annotation.task_id ||
    revision.revision !== selected.revision ||
    revision.content_hash !== selected.content_hash ||
    !sameAnnotationDataRef(revision.data_ref, overview.data) ||
    new Set(revision.tags.map((tag) => tag.annotation_id)).size !==
      revision.tags.length ||
    revision.tags.some(
      (tag) =>
        tag.start_step >= tag.end_step ||
        tag.end_step > overview.data.frame_count,
    )
  )
    throw new Error(
      "标注与当前 Episode 的数据版本或保存修订不一致，请刷新重试。",
    );

  const labels = new Map<string, string>();
  if (revision.tags.some((tag) => !tag.label?.trim())) {
    const schema = schemaWire.parse(
      await request({
        method: "GET",
        path: `/projects/${encodeURIComponent(scope.projectId!)}/tag-schemas/${encodeURIComponent(revision.tag_schema_id)}/versions/${revision.tag_schema_version}`,
        scope,
        signal,
        cache: "no-store",
      }),
    );
    if (
      schema.project_id !== scope.projectId ||
      schema.schema_id !== revision.tag_schema_id ||
      schema.version !== revision.tag_schema_version
    )
      throw new Error("Tag 名称与保存时的标注定义不一致。");
    schema.document.nodes.forEach((node) =>
      labels.set(node.tag_id, node.display_name),
    );
  }
  const start = BigInt(startNs),
    end = BigInt(endNs);
  const stepToNs = (value: number) => {
    const point =
      start + BigInt(stepToTimelineNs(value, overview.frequency_hz));
    return (
      value === overview.data.frame_count || point > end ? end : point
    ).toString();
  };
  return {
    count: revision.tags.length,
    tracks: buildTagTimelineTracks({
      tags: revision.tags,
      schemaLabels: labels,
      stepToNs,
    }),
  };
}
