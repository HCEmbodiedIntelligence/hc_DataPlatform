import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { loadEpisodeAnnotationTracks } from "../../features/datasets/episodes/episode-annotations";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import type { Scope } from "../../shared/scope/model";

/** Read the exact saved revision associated with the displayed Episode version. */
export function useEpisodeAnnotations({
  scope,
  data,
  canRead,
  canReadAnnotations,
  startNs,
  endNs,
}: {
  scope: Scope | null;
  data: ReviewDataRef | null;
  canRead: boolean;
  canReadAnnotations: boolean;
  startNs: string;
  endNs: string;
}) {
  const key = JSON.stringify([scope, data]);
  const [selection, setSelection] = useState<{
    key: string;
    taskId: string;
  } | null>(null);
  const selectedTaskId = selection?.key === key ? selection.taskId : undefined;
  const allowed = canRead && canReadAnnotations;
  const query = useQuery({
    queryKey: [
      "episode-viewer-annotations",
      scope,
      data,
      selectedTaskId,
      startNs,
      endNs,
    ],
    enabled: Boolean(scope && data && allowed),
    queryFn: async ({ signal }) => {
      if (!scope || !data) throw new Error("缺少 Episode 数据版本。");
      const overview = await loadEpisodeOverview(
        scope,
        data,
        { annotationTaskId: selectedTaskId },
        signal,
      );
      if (overview.annotation.availability !== "AVAILABLE")
        return { overview, tracks: [], count: 0 };
      return {
        overview,
        ...(await loadEpisodeAnnotationTracks(
          scope,
          overview,
          startNs,
          endNs,
          signal,
        )),
      };
    },
  });
  const value = allowed && !query.isError ? query.data : undefined;
  return {
    tracks: value?.tracks ?? [],
    dataBound: data !== null,
    allowed,
    isPending: Boolean(scope && data && allowed && query.isPending),
    error: allowed ? query.error : null,
    annotation: value?.overview.annotation,
    tagCount: value?.count ?? 0,
    selectedTaskId,
    selectTask: (taskId: string) =>
      setSelection(taskId ? { key, taskId } : null),
    refetch: query.refetch,
  };
}

export type EpisodeAnnotations = ReturnType<typeof useEpisodeAnnotations>;
