import type { components } from "../../../shared/api/generated/platform";
import type { ViewerTimelineTrack } from "../../viewer/EpisodeWorkbenchCore";

type RuntimeAnnotationTag = Pick<
  components["schemas"]["AnnotationTag"],
  | "annotation_id"
  | "tag_id"
  | "path"
  | "label"
  | "parent_annotation_id"
  | "start_step"
  | "end_step"
>;

/** Preserve hierarchy and place overlapping intervals on separate lanes. */
export function buildTagTimelineTracks({
  tags: values,
  schemaLabels,
  stepToNs,
  idPrefix = "tag-intervals",
  labelPrefix = "Tag",
  tone = "tag-level",
}: {
  tags: readonly RuntimeAnnotationTag[];
  schemaLabels: ReadonlyMap<string, string>;
  stepToNs: (step: number) => string;
  idPrefix?: string;
  labelPrefix?: string;
  tone?: "phase" | "action" | "tag-level";
}): readonly ViewerTimelineTrack[] {
  const byId = new Map(values.map((tag) => [tag.annotation_id, tag]));
  const depthCache = new Map<string, number>();
  const pathCache = new Map<string, readonly string[]>();
  const depthFor = (
    tag: RuntimeAnnotationTag,
    seen = new Set<string>(),
  ): number => {
    const cached = depthCache.get(tag.annotation_id);
    if (cached !== undefined) return cached;
    if (!tag.parent_annotation_id || seen.has(tag.annotation_id)) return 0;
    const parent = byId.get(tag.parent_annotation_id);
    if (!parent) return 0;
    const depth = depthFor(parent, new Set(seen).add(tag.annotation_id)) + 1;
    depthCache.set(tag.annotation_id, depth);
    return depth;
  };
  const pathFor = (
    tag: RuntimeAnnotationTag,
    seen = new Set<string>(),
  ): readonly string[] => {
    const cached = pathCache.get(tag.annotation_id);
    if (cached) return cached;
    const label =
      tag.label?.trim() ||
      schemaLabels.get(tag.tag_id) ||
      tag.path.at(-1) ||
      tag.tag_id;
    if (!tag.parent_annotation_id || seen.has(tag.annotation_id)) {
      const path = tag.label
        ? [label]
        : tag.path.map((tagId) => schemaLabels.get(tagId) ?? tagId);
      pathCache.set(tag.annotation_id, path);
      return path;
    }
    const parent = byId.get(tag.parent_annotation_id);
    const path = parent
      ? [...pathFor(parent, new Set(seen).add(tag.annotation_id)), label]
      : [label];
    pathCache.set(tag.annotation_id, path);
    return path;
  };
  const byDepth = new Map<number, RuntimeAnnotationTag[]>();
  for (const tag of values) {
    const depth = depthFor(tag);
    const level = byDepth.get(depth) ?? [];
    level.push(tag);
    byDepth.set(depth, level);
  }
  const tagLevelTones = [
    "tag-level-1",
    "tag-level-2",
    "tag-level-3",
    "tag-level-4",
  ] as const;
  return [...byDepth.entries()]
    .toSorted(([left], [right]) => left - right)
    .flatMap(([depth, level]) => {
      const lanes: RuntimeAnnotationTag[][] = [];
      for (const tag of level.toSorted(
        (left, right) =>
          left.start_step - right.start_step || left.end_step - right.end_step,
      )) {
        const lane = lanes.find(
          (candidate) =>
            (candidate.at(-1)?.end_step ?? Number.NEGATIVE_INFINITY) <=
            tag.start_step,
        );
        if (lane) lane.push(tag);
        else lanes.push([tag]);
      }
      return lanes.map(
        (lane, laneIndex) =>
          ({
            id: `${idPrefix}-level-${depth + 1}-lane-${laneIndex + 1}`,
            label: `${labelPrefix} L${depth + 1}${laneIndex ? ` · ${laneIndex + 1}` : ""}`,
            level: depth,
            segments: lane.map((tag) => ({
              id: `${idPrefix}-${tag.annotation_id}`,
              label: pathFor(tag).join(" / "),
              startNs: stepToNs(tag.start_step),
              endNs: stepToNs(tag.end_step),
              activatePlayback: true,
              tone:
                tone === "tag-level"
                  ? tagLevelTones[depth % tagLevelTones.length]
                  : tone,
            })),
          }) satisfies ViewerTimelineTrack,
      );
    });
}
