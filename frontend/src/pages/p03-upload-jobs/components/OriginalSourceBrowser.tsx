import { useQuery } from "@tanstack/react-query";
import { Alert, Button, InputNumber, Space, Table } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { createPlaybackClock } from "../../../features/viewer/PlaybackClock";
import { EpisodeWorkbenchCore } from "../../../features/viewer/EpisodeWorkbenchCore";
import type { StreamDescriptor } from "../../../features/viewer/types";
import { nativeRoot } from "../lerobot-processing";
import { formatBytes } from "../upload-contract";

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
    <EpisodeWorkbenchCore
      episodeId={`original-${episode.episode_index}`}
      datasetId={importId}
      versionId={`original-${episode.episode_index}`}
      clock={clock}
      streams={streams}
      mode="readonly"
      timelineLabel="原始视频时间轴"
    />
  );
}

export default function OriginalSourceBrowser({
  scope,
  importId,
}: {
  scope: IngestScope;
  importId: string;
}) {
  const [offset, setOffset] = useState(0);
  const [episodeIndex, setEpisodeIndex] = useState(0);
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
    <Space orientation="vertical" style={{ width: "100%" }} size="middle">
      <p>文件内容和目录保持原样。预览直接读取原视频，浏览器需支持其编码。</p>
      {files.isError ? (
        <Alert
          type="error"
          title="原始文件读取失败"
          action={<Button onClick={() => void files.refetch()}>重试</Button>}
        />
      ) : null}
      {(files.data?.episode_count ?? 0) > 0 ? (
        <label>
          Episode（从 0 开始）{" "}
          <InputNumber
            min={0}
            max={files.data!.episode_count - 1}
            value={episodeIndex}
            aria-label="原始 Episode"
            onChange={(value) => {
              if (value !== null) setEpisodeIndex(value);
            }}
          />
        </label>
      ) : null}
      {episode.isError ? (
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
      {episode.data ? (
        <OriginalEpisodePlayer
          key={`${importId}:${episodeIndex}`}
          scope={scope}
          importId={importId}
          episode={episode.data}
        />
      ) : null}
      {downloadError ? <Alert type="error" title={downloadError} /> : null}
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
              <Button onClick={() => void download(file)}>下载原文件</Button>
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
    </Space>
  );
}
