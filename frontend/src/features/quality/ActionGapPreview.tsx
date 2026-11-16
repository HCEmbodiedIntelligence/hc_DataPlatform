import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Select, Space, Spin, Typography } from "antd";
import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import type { components } from "../../shared/api/generated/platform";
import type { AutoQualityProblem } from "../cleaning/api/auto-quality-problems.queries";
import { DataVisualizationWorkbench } from "../viewer";
import { buildRecordingCameraStreams } from "../viewer/recording-camera-streams";
import { usePlaybackClock } from "../viewer/use-playback-clock";

type Source = components["schemas"]["RecordingVideoSource"];
type Sources = components["schemas"]["RecordingVideoSourceEnvelope"];
type Gap = {
  first_frame: number;
  last_frame: number;
  first_offset_ns: number;
  last_offset_ns: number;
  origin: string;
};
const seconds = (ns: number) => (ns / 1e9).toFixed(3);
const frameLabel = (gap: Gap) =>
  gap.first_frame === gap.last_frame
    ? String(gap.first_frame)
    : `${gap.first_frame}–${gap.last_frame}`;
const fillLabel = (gap: Gap) =>
  ["next_frame_state", "terminal_state"].includes(gap.origin)
    ? "旧规则已补写"
    : "缺失未补写";

function GapVideo({
  gap,
  source,
  refresh,
}: {
  gap: Gap;
  source: Source;
  refresh: (id: string, signal: AbortSignal) => Promise<Source>;
}) {
  // The unfiltered RGB file has one CFR video frame per training frame.
  // QC offsets use the original acquisition clock, which may be irregular.
  const frameTime = (frame: number) => Math.round((frame * 1e9) / source.fps);
  const first = frameTime(gap.first_frame);
  const after = frameTime(gap.last_frame + 1);
  const begin = Math.max(0, first - 1e9);
  const end = Math.max(
    after,
    Math.min(Number(source.duration_ns), after + 1e9),
  );
  const clock = usePlaybackClock(
    String(begin),
    String(end),
    `${source.asset_id}/${first}`,
  );
  useEffect(() => {
    clock.seek(String(first));
  }, [clock, first]);
  const streams = useMemo(
    () =>
      buildRecordingCameraStreams({
        sources: [source],
        refreshSource: refresh,
      }),
    [source, refresh],
  );
  return (
    <>
      <DataVisualizationWorkbench
        layout="preview"
        showNavigation={false}
        adapter={{
          id: `qc-gap-${source.asset_id}-${gap.first_frame}`,
          mode: "episode-slicing",
          title: "原始彩色视频",
          readOnly: true,
          clock,
          cameraStreams: streams,
          collectionItems: [],
          findings: [],
          actions: [],
          timelineTracks: [
            {
              id: "gap",
              label: "动作缺失",
              segments: [
                {
                  id: "current-gap",
                  label: fillLabel(gap),
                  tone: "issue",
                  startNs: String(first),
                  endNs: String(after),
                  activatePlayback: true,
                },
              ],
            },
          ],
        }}
      />
      <Button
        onClick={() => {
          clock.seek(String(begin));
          clock.play();
        }}
      >
        播放片段及前后 1 秒
      </Button>
    </>
  );
}

export function ActionGapPreview({
  scope,
  problem,
}: {
  scope: IngestScope;
  problem: AutoQualityProblem;
}) {
  const [index, setIndex] = useState(0);
  const [camera, setCamera] = useState<string>();
  const base = `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(problem.recordingId!)}`;
  const scopeKey = [
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    problem.recordingId,
  ];
  const gaps = useQuery({
    queryKey: ["qc-action-gaps", ...scopeKey, problem.reportSha256],
    queryFn: ({ signal }) =>
      request<Gap[]>({
        method: "GET",
        scope,
        signal,
        path: `${base}/quality-reports/${problem.reportSha256}/action-gaps`,
      }),
  });
  const fetchSources = (signal?: AbortSignal) =>
    request<Sources>({
      method: "GET",
      scope,
      signal,
      path: `${base}/video-sources`,
      query: { original_rgb: "true" },
      cache: "no-store",
    });
  const videos = useQuery({
    queryKey: ["qc-original-rgb", ...scopeKey],
    queryFn: ({ signal }) => fetchSources(signal),
    retry: 1,
  });
  const refresh = useMemo(
    () => async (id: string, signal: AbortSignal) => {
      const page = await request<Sources>({
        method: "GET",
        scope,
        signal,
        path: `${base}/video-sources`,
        query: { original_rgb: "true" },
        cache: "no-store",
      });
      const fresh = page.sources.find((item) => item.asset_id === id);
      if (!fresh) throw new Error("当前相机的原始视频不可用");
      return fresh;
    },
    [base, scope],
  );
  const source =
    videos.data?.sources.find((v) => v.asset_id === camera) ??
    videos.data?.sources[0];
  const segments = gaps.data ?? [];
  const gap = segments[index];
  return (
    <Card size="small" title="问题片段">
      <Space orientation="vertical" style={{ width: "100%" }}>
        <Typography.Text type="danger">校验失败 · 中间帧缺动作</Typography.Text>
        {gaps.isPending && <Spin description="正在读取问题帧" />}
        {gaps.isError && (
          <Alert
            type="error"
            title="问题片段读取失败"
            action={<Button onClick={() => void gaps.refetch()}>重试</Button>}
          />
        )}
        {gap && (
          <>
            <Space wrap>
              <Select
                aria-label="选择问题片段"
                value={index}
                onChange={setIndex}
                style={{ minWidth: 245 }}
                options={segments.map((g, i) => ({
                  value: i,
                  label: `片段 ${i + 1}/${segments.length} · 第 ${frameLabel(g)} 帧`,
                }))}
              />
              <Button disabled={!index} onClick={() => setIndex(index - 1)}>
                上一段
              </Button>
              <Button
                disabled={index + 1 >= segments.length}
                onClick={() => setIndex(index + 1)}
              >
                下一段
              </Button>
              {(videos.data?.sources.length ?? 0) > 1 && (
                <Select
                  aria-label="相机"
                  value={source?.asset_id}
                  onChange={setCamera}
                  options={videos.data!.sources.map((v) => ({
                    value: v.asset_id,
                    label: v.camera_id,
                  }))}
                />
              )}
            </Space>
            <Typography.Text>
              {fillLabel(gap)} · {gap.last_frame - gap.first_frame + 1} 帧 ·
              录制时间 {seconds(gap.first_offset_ns)}
              {gap.first_frame !== gap.last_frame &&
                `–${seconds(gap.last_offset_ns)}`}{" "}
              秒（帧号从 0 起）
            </Typography.Text>
            {videos.isPending && <Spin description="正在读取原始视频" />}
            {videos.isError && (
              <Alert
                type="warning"
                title="原始视频读取失败"
                action={
                  <Button onClick={() => void videos.refetch()}>重试</Button>
                }
              />
            )}
            {videos.isSuccess && !source && (
              <Typography.Text>这条录制没有彩色相机视频。</Typography.Text>
            )}
            {source && <GapVideo gap={gap} source={source} refresh={refresh} />}
          </>
        )}
      </Space>
    </Card>
  );
}
