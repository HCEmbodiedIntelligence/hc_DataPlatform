import { useQuery } from "@tanstack/react-query";
import { Alert, Button, InputNumber, Space, Spin } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { useScopeQueryRevision } from "../../../shared/api/use-scoped-query";
import {
  getOriginalFiles,
  getOriginalEpisode,
  authorizeOriginalFile,
  originalPreviewReason,
  type OriginalEpisode,
} from "./original-source-api";
import { createPlaybackClock } from "../../viewer/PlaybackClock";
import { DataVisualizationWorkbench } from "../../viewer/DataVisualizationWorkbench";
import type { StreamDescriptor } from "../../viewer/types";
import OriginalFileExplorer from "./OriginalFileExplorer";
import styles from "./OriginalSourceBrowser.module.css";

export function OriginalEpisodePlayer({
  scope,
  importId,
  episode,
}: {
  scope: IngestScope;
  importId: string;
  episode: OriginalEpisode;
}) {
  const clock = useMemo(
    () => createPlaybackClock({ startNs: "0", endNs: episode.duration_ns }),
    [episode],
  );
  const mountedClock = useRef<typeof clock | null>(null);
  useEffect(() => {
    mountedClock.current = clock;
    return () => {
      mountedClock.current = null;
      // StrictMode remounts effects with the same clock before this runs.
      queueMicrotask(() => {
        if (mountedClock.current !== clock) clock.dispose();
      });
    };
  }, [clock]);
  const streams = useMemo<StreamDescriptor[]>(
    () =>
      episode.videos
        .filter(
          (video) =>
            video.preview_kind === "ORIGINAL_VIDEO" &&
            video.media_kind === "RGB",
        )
        .map((video) => {
          const authorize = async (signal: AbortSignal) => {
            const grant = await authorizeOriginalFile(
              scope,
              importId,
              video.path,
              false,
              signal,
            );
            return {
              url: grant.url,
              expiresAt: grant.expires_at,
              kind: "rgb-video" as const,
            };
          };
          return {
            id: video.camera_id,
            canonicalPath: video.camera_id,
            displayName: video.camera_id,
            modality: "rgb",
            schema: { id: "original-video", version: "1" },
            rateHz: episode.fps,
            startNs: "0",
            endNs: episode.duration_ns,
            availability: "ready",
            mediaStartSeconds: video.start_seconds,
            mediaEndSeconds: video.end_seconds,
            mediaSource: { authorize, refresh: authorize },
          };
        }),
    [episode, importId, scope],
  );
  return (
    <>
      {episode.videos
        .filter((video) => video.preview_kind === "DOWNLOAD_ONLY")
        .map((video) => (
          <Alert
            key={video.camera_id}
            type="info"
            showIcon
            title={video.camera_id}
            description={originalPreviewReason(video)}
          />
        ))}
      {streams.length > 0 ? (
        <DataVisualizationWorkbench
          layout="preview"
          adapter={{
            id: `original-${importId}-${episode.episode_index}`,
            mode: "raw-diagnostic",
            title: `原始视频 · Episode ${episode.episode_index}`,
            description: `${episode.frame_count} 帧 · ${episode.fps} FPS`,
            readOnly: true,
            clock,
            cameraStreams: streams,
            collectionItems: [],
            findings: [],
            actions: [],
            timelineLabel: "原始视频时间轴",
            timelineTracks: [],
          }}
        />
      ) : null}
    </>
  );
}

export default function OriginalSourceBrowser({
  scope,
  importId,
  initialEpisodeIndex = 0,
  filesOnly = false,
  canDownload = true,
}: {
  scope: IngestScope;
  importId: string;
  initialEpisodeIndex?: number;
  filesOnly?: boolean;
  canDownload?: boolean;
}) {
  const [episodeIndex, setEpisodeIndex] = useState(initialEpisodeIndex);
  const revision = useScopeQueryRevision();
  const identity = [
    "original-source",
    revision,
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    importId,
  ];
  const files = useQuery({
    queryKey: [...identity, "files"],
    enabled: !filesOnly,
    queryFn: ({ signal }) => getOriginalFiles(scope, importId, 0, signal),
    retry: false,
    staleTime: 60_000,
  });
  const episode = useQuery({
    queryKey: [...identity, "episode", episodeIndex],
    enabled:
      !filesOnly &&
      (files.data?.episode_count ?? 0) > 0 &&
      files.data?.source_format === "LEROBOT_V3",
    queryFn: ({ signal }) =>
      getOriginalEpisode(scope, importId, episodeIndex, signal),
    retry: false,
    staleTime: 60_000,
  });
  return (
    <div className={styles.browser}>
      {files.isError ? (
        <Alert
          type="error"
          title="原始文件读取失败"
          action={<Button onClick={() => void files.refetch()}>重试</Button>}
        />
      ) : null}
      {!filesOnly &&
      (files.data?.episode_count ?? 0) > 0 &&
      files.data?.source_format === "LEROBOT_V3" ? (
        <div className={styles.episodeToolbar}>
          <Space size="small" wrap>
            <Button
              size="small"
              disabled={episodeIndex <= 0}
              onClick={() => setEpisodeIndex((index) => index - 1)}
            >
              上一条
            </Button>
            <label className={styles.episodeLabel}>
              Episode
              <InputNumber
                size="small"
                min={0}
                max={files.data!.episode_count - 1}
                precision={0}
                value={episodeIndex}
                aria-label="原始 Episode"
                onChange={(value) => {
                  if (
                    value !== null &&
                    Number.isInteger(value) &&
                    value >= 0 &&
                    value < files.data!.episode_count
                  )
                    setEpisodeIndex(value);
                }}
              />
            </label>
            <Button
              size="small"
              disabled={episodeIndex >= files.data!.episode_count - 1}
              onClick={() => setEpisodeIndex((index) => index + 1)}
            >
              下一条
            </Button>
            <span className={styles.meta}>
              共 {files.data!.episode_count} 条 · 从 0 开始
            </span>
          </Space>
        </div>
      ) : null}
      {!filesOnly && episode.isError ? (
        <Alert
          type="warning"
          title="此 Episode 暂时无法预览"
          description={
            episode.error instanceof Error
              ? episode.error.message
              : "原始文件仍可下载。"
          }
          action={
            <Button onClick={() => void episode.refetch()}>重试预览</Button>
          }
        />
      ) : null}
      {!filesOnly && episode.isFetching && !episode.data ? (
        <div className={styles.loading}>
          <Spin size="small" /> 正在加载视频…
        </div>
      ) : null}
      {!filesOnly && episode.data ? (
        <OriginalEpisodePlayer
          key={`${importId}:${episodeIndex}`}
          scope={scope}
          importId={importId}
          episode={episode.data}
        />
      ) : null}
      <OriginalFileExplorer
        scope={scope}
        importId={importId}
        canDownload={canDownload}
      />
    </div>
  );
}
