import { useQuery } from "@tanstack/react-query";
import { Alert, Button, InputNumber, Space, Spin, Table } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { createPlaybackClock } from "../../../features/viewer/PlaybackClock";
import { DataVisualizationWorkbench } from "../../../features/viewer/DataVisualizationWorkbench";
import type { StreamDescriptor } from "../../../features/viewer/types";
import { nativeRoot } from "../lerobot-processing";
import { formatBytes } from "../upload-contract";
import styles from "./OriginalSourceBrowser.module.css";

interface OriginalFile {
  path: string;
  size: number;
  sha256: string;
}
interface FilePage {
  files: OriginalFile[];
  total: number;
  episode_count: number;
  source_format: string;
}
interface Episode {
  episode_index: number;
  frame_count: number;
  fps: number;
  duration_ns: string;
  videos: {
    camera_id: string;
    path: string;
    start_seconds: number;
    end_seconds: number;
  }[];
}
interface FileGrant {
  url: string;
  expires_at: string;
}

export function OriginalEpisodePlayer({
  scope,
  importId,
  episode,
}: {
  scope: IngestScope;
  importId: string;
  episode: Episode;
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
      episode.videos.map((video) => {
        const authorize = async (signal: AbortSignal) => {
          const grant = await request<FileGrant>({
            method: "GET",
            scope,
            signal,
            path: `${nativeRoot(scope)}/${encodeURIComponent(importId)}/assets:read`,
            query: { path: video.path },
          });
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
  const [offset, setOffset] = useState(0);
  const [episodeIndex, setEpisodeIndex] = useState(initialEpisodeIndex);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const base = `${nativeRoot(scope)}/${encodeURIComponent(importId)}`;
  const identity = [
    "original-source",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    importId,
  ];
  const files = useQuery({
    queryKey: [...identity, "files", offset],
    queryFn: ({ signal }) =>
      request<FilePage>({
        method: "GET",
        path: `${base}/files`,
        scope,
        signal,
        query: { offset, limit: 50 },
      }),
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
      request<Episode>({
        method: "GET",
        scope,
        signal,
        path: `${base}/episodes/${episodeIndex}`,
      }),
    retry: false,
    staleTime: 60_000,
  });
  const download = async (file: OriginalFile) => {
    setDownloadError(null);
    try {
      const grant = await request<FileGrant>({
        method: "GET",
        path: `${base}/assets:read`,
        scope,
        query: { path: file.path, download: true },
      });
      const link = document.createElement("a");
      link.href = grant.url;
      link.download = file.path.split("/").at(-1) ?? file.path;
      link.rel = "noopener noreferrer";
      document.body.append(link);
      link.click();
      link.remove();
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "下载授权失败");
    }
  };
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
      {downloadError ? <Alert type="error" title={downloadError} /> : null}
      <details
        className={styles.files}
        open={filesOnly || files.data?.episode_count === 0}
      >
        <summary>原始文件（{files.data?.total ?? 0}）</summary>
        <Table<OriginalFile>
          size="small"
          rowKey="path"
          dataSource={files.data?.files ?? []}
          loading={files.isPending}
          pagination={false}
          scroll={{ x: true }}
          columns={[
            { title: "原始路径", dataIndex: "path" },
            {
              title: "大小",
              dataIndex: "size",
              render: (size: number) => formatBytes(size),
            },
            { title: "SHA-256", dataIndex: "sha256", ellipsis: true },
            {
              title: "下载",
              key: "download",
              render: (_: unknown, file: OriginalFile) => (
                <Button
                  disabled={!canDownload}
                  onClick={() => void download(file)}
                >
                  下载原文件
                </Button>
              ),
            },
          ]}
        />
        <Space>
          <Button
            disabled={offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - 50))}
          >
            上一页
          </Button>
          <span>共 {files.data?.total ?? 0} 个文件</span>
          <Button
            disabled={offset + 50 >= (files.data?.total ?? 0)}
            onClick={() => setOffset((value) => value + 50)}
          >
            下一页
          </Button>
        </Space>
      </details>
    </div>
  );
}
