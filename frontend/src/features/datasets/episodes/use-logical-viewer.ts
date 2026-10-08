import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { makeScopeKey } from "../../../shared/scope/model";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  fetchLogicalSnapshot,
  type LogicalSelection,
} from "../api/logical-data";
import type { EpisodeRevisionWire } from "../api/wire-schemas";
import { adaptLogicalViewerStreams } from "./logical-viewer";

export function useLogicalViewer(
  revision: EpisodeRevisionWire | undefined,
  snapshotToken: string | undefined,
  enabled: boolean,
) {
  const sessionRevision = useShellStore((s) => s.sessionRevision);
  const capabilityRevision = useShellStore((s) => s.capabilityRevision);
  const selection = useMemo(
    (): LogicalSelection | null =>
      revision && snapshotToken
        ? {
            scope: {
              organizationId: revision.scope.organization_id,
              projectId: revision.scope.project_id,
              regionCode: revision.scope.region_code,
            },
            datasetId: revision.dataset_id,
            versionId: revision.version_id,
            revisionId: revision.revision_id,
            episodeId: revision.episode_id,
            contentSha256: revision.content_sha256,
            snapshotToken,
          }
        : null,
    [revision, snapshotToken],
  );
  const query = useQuery({
    queryKey: [
      "datasets",
      selection ? makeScopeKey(selection.scope) : null,
      "logical-snapshot",
      selection,
      sessionRevision,
      capabilityRevision,
    ],
    enabled: enabled && selection !== null,
    queryFn: ({ signal }) => fetchLogicalSnapshot(selection!, signal),
  });
  const streams = useMemo(
    () =>
      enabled && selection && query.data
        ? adaptLogicalViewerStreams(selection, query.data)
        : [],
    [enabled, selection, query.data, sessionRevision, capabilityRevision],
  );
  const robotTarget = useMemo(
    () =>
      enabled && selection && query.data
        ? { selection, view: query.data }
        : null,
    [enabled, selection, query.data],
  );
  return { ...query, streams, robotTarget };
}
