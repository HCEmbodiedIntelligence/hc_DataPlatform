import type { RuntimeAnnotationTag } from "./runtime-annotation-adapter";

type IntervalRange = Pick<RuntimeAnnotationTag, "start_step" | "end_step"> &
  Partial<Pick<RuntimeAnnotationTag, "annotation_id">>;

/** The closest longer overlapping interval is the parent; touching ends do not overlap. */
export function findIntervalParent(
  tags: readonly RuntimeAnnotationTag[],
  range: IntervalRange,
): RuntimeAnnotationTag | undefined {
  const span = range.end_step - range.start_step;
  return tags
    .filter(
      (tag) =>
        tag.annotation_id !== range.annotation_id &&
        tag.end_step - tag.start_step > span &&
        tag.start_step < range.end_step &&
        range.start_step < tag.end_step,
    )
    .toSorted(
      (left, right) =>
        left.end_step - left.start_step - (right.end_step - right.start_step) ||
        left.start_step - right.start_step ||
        left.annotation_id.localeCompare(right.annotation_id),
    )[0];
}

/** Recompute all levels so later additions, resizes and moves have the same result. */
export function reconcileTagHierarchy(
  tags: readonly RuntimeAnnotationTag[],
): readonly RuntimeAnnotationTag[] {
  return rebuildManualPaths(
    tags.map((tag) => {
      const parentId = findIntervalParent(tags, tag)?.annotation_id ?? null;
      return (tag.parent_annotation_id ?? null) === parentId
        ? tag
        : { ...tag, parent_annotation_id: parentId };
    }),
  );
}

/** Schema paths stay intact; named intervals follow their temporal parent. */
function rebuildManualPaths(
  tags: readonly RuntimeAnnotationTag[],
): readonly RuntimeAnnotationTag[] {
  const byId = new Map(tags.map((tag) => [tag.annotation_id, tag]));
  const paths = new Map<string, string[]>();
  const resolvePath = (
    tag: RuntimeAnnotationTag,
    seen = new Set<string>(),
  ): string[] => {
    if (tag.label == null || seen.has(tag.annotation_id)) return tag.path;
    const cached = paths.get(tag.annotation_id);
    if (cached) return cached;
    seen.add(tag.annotation_id);
    const parent = tag.parent_annotation_id
      ? byId.get(tag.parent_annotation_id)
      : undefined;
    const path = [...(parent ? resolvePath(parent, seen) : []), tag.tag_id];
    paths.set(tag.annotation_id, path);
    return path;
  };
  return tags.map((tag) => {
    const path = resolvePath(tag);
    return path.length === tag.path.length &&
      path.every((id, index) => id === tag.path[index])
      ? tag
      : { ...tag, path };
  });
}

export function updateIntervalTag(
  tags: readonly RuntimeAnnotationTag[],
  annotationId: string,
  patch: Pick<RuntimeAnnotationTag, "label" | "start_step" | "end_step">,
): readonly RuntimeAnnotationTag[] {
  return reconcileTagHierarchy(
    tags.map((tag) =>
      tag.annotation_id === annotationId ? { ...tag, ...patch } : tag,
    ),
  );
}

/** Delete only the selected interval and regroup the remaining time ranges. */
export function removeIntervalTag(
  tags: readonly RuntimeAnnotationTag[],
  annotationId: string,
): readonly RuntimeAnnotationTag[] {
  const removed = tags.find((tag) => tag.annotation_id === annotationId);
  if (!removed) return tags;
  return reconcileTagHierarchy(
    tags.filter((tag) => tag.annotation_id !== annotationId),
  );
}
