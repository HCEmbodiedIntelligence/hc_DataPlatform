import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Select, Space } from "antd";
import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import { qualityFindingText } from "../../shared/lib/quality-presentation";
import type { PlaybackClock } from "../viewer/PlaybackClock";

export interface ReportTarget {
  report: string;
  code: string;
  recordingId?: string;
  rolloutId?: string;
}
interface Location {
  topic: string;
  start_ns: string;
  end_ns: string;
  first_frame: number | null;
  last_frame: number | null;
}
export function useProblemLocations(
  scope: IngestScope | null,
  target: ReportTarget | undefined,
  clock: PlaybackClock,
) {
  const [selection, setSelection] = useState(0);
  const query = useQuery({
    queryKey: ["quality-locations", scope, target],
    enabled: Boolean(scope && target),
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      request<Location[]>({
        method: "GET",
        scope: scope!,
        signal,
        path: `/projects/${encodeURIComponent(scope!.projectId)}/regions/${encodeURIComponent(scope!.regionCode)}/quality-problems/${encodeURIComponent(target!.report)}/locations`,
        query: {
          code: target!.code,
          recordingId: target!.recordingId,
          rolloutId: target!.rolloutId,
        },
      }),
  });
  useEffect(() => {
    setSelection(0);
  }, [target?.report, target?.code]);
  const current = query.data?.[selection];
  useEffect(() => {
    if (current) clock.seek(current.start_ns);
  }, [clock, current]);
  const name =
    target &&
    (qualityFindingText(target.code).title === "数据质量异常"
      ? target.code
      : qualityFindingText(target.code).title);
  const tracks = useMemo(
    () =>
      !query.data?.length
        ? []
        : [
            {
              id: "quality-findings",
              label: "质检异常",
              segments: query.data.map((item, i) => ({
                id: `finding-${i}`,
                label: `${name} · ${item.topic}`,
                tone: "issue" as const,
                startNs: item.start_ns,
                endNs: String(
                  BigInt(item.end_ns) > BigInt(item.start_ns)
                    ? BigInt(item.end_ns)
                    : BigInt(item.start_ns) + 1n,
                ),
                activatePlayback: true,
              })),
            },
          ],
    [name, query.data],
  );
  const label = (loc: Location) =>
    [
      loc.topic,
      loc.first_frame != null
        ? `第 ${loc.first_frame}–${loc.last_frame} 帧`
        : null,
      `${(Number(loc.start_ns) / 1e9).toFixed(3)}–${(Number(loc.end_ns) / 1e9).toFixed(3)} 秒`,
    ]
      .filter(Boolean)
      .join(" · ");
  const panel = target && (
    <Space wrap size="small" aria-label="问题定位">
      <strong>{name}</strong>
      {query.isPending ? (
        "正在定位…"
      ) : query.isError ? (
        <Alert type="error" title="质检位置读取失败" />
      ) : current ? (
        <Select
          aria-label="异常位置"
          value={selection}
          onSelect={(i) => {
            setSelection(i);
            clock.seek(query.data![i]!.start_ns);
          }}
          options={query.data!.map((loc, i) => ({
            value: i,
            label: label(loc),
          }))}
          style={{ minWidth: 240 }}
        />
      ) : (
        "该报告未提供具体异常帧，请结合质检明细查看。"
      )}
    </Space>
  );
  return { tracks, panel };
}
