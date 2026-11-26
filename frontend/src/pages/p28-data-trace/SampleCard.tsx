import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Select, Space, Tag } from "antd";
import { Link, useLocation } from "react-router-dom";
import type { Scope } from "../../shared/scope/model";
import { decodedFileName } from "../../shared/lib/resource-labels";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import { loadEpisodeAnnotationTracks } from "../../features/datasets/episodes/episode-annotations";
import { isDatasetId } from "../../entities/dataset";
import { isDatasetVersionId } from "../../entities/dataset-version";
import { isEpisodeId } from "../../entities/episode";
import { routes } from "../../features/datasets/routing";
import type { Sample } from "./api";
import styles from "./styles.module.css";

export function SampleCard({
  title,
  sample,
  scope,
  canReadTags,
}: {
  title: string;
  sample: Sample;
  scope: Scope;
  canReadTags: boolean;
}) {
  const location = useLocation();
  const data = sample.data;
  const [task, setTask] = useState<string>();
  const overview = useQuery({
    queryKey: [
      "trace-overview",
      scope,
      data?.data_version_id,
      canReadTags,
      task,
    ],
    enabled: Boolean(data),
    queryFn: ({ signal }) =>
      loadEpisodeOverview(scope, data!, { annotationTaskId: task }, signal),
  });
  const info = overview.data;
  const ann = info?.annotation;
  const tags = useQuery({
    queryKey: [
      "trace-tags",
      scope,
      data?.data_version_id,
      ann?.current_revision,
    ],
    enabled: Boolean(
      canReadTags && ann?.availability === "AVAILABLE" && ann.current_revision,
    ),
    queryFn: ({ signal }) =>
      loadEpisodeAnnotationTracks(
        scope,
        info!,
        "0",
        String(Math.max(1, Math.round(info!.duration_seconds * 1e9))),
        signal,
      ),
  });
  const labels = [
    ...new Set(
      tags.data?.tracks.flatMap((track) => track.segments.map((s) => s.label)),
    ),
  ];
  const id = data?.identity;
  const version = sample.viewer_version_id;
  const playback =
    id &&
    isDatasetId(id.dataset_id) &&
    isEpisodeId(id.episode_id) &&
    isDatasetVersionId(version)
      ? routes.episodeViewer.build({
          datasetId: id.dataset_id,
          episodeId: id.episode_id,
          versionId: version,
          returnTo: location.pathname + location.search,
        })
      : null;
  return (
    <Card
      size="small"
      title={title}
      extra={playback && <Link to={playback}>打开同步回放</Link>}
    >
      {data && (
        <div className={styles.identifier}>
          {id?.episode_id} · v{data.version}
        </div>
      )}
      {overview.isFetching && !info && <p role="status">正在读取样本…</p>}
      {overview.error && (
        <Alert
          type="error"
          title="样本详情读取失败"
          description={overview.error.message}
        />
      )}
      {info && (
        <>
          <strong>{decodedFileName(info.display_name)}</strong>
          <p>
            {info.recording_date} · {info.duration_seconds.toFixed(2)} 秒 ·{" "}
            {info.data.frame_count} 帧 · {info.camera_stream_count} 路视频
          </p>
          <dl className={styles.facts}>
            <dt>采集指令</dt>
            <dd>
              {info.task.instructions.filter(Boolean).join("；") || "未记录"}
            </dd>
            <dt>自动质检</dt>
            <dd>
              {
                {
                  PASS: "通过",
                  RISK: "风险",
                  REJECT: "拒绝",
                  UNKNOWN: "暂无报告",
                }[info.quality.status]
              }
            </dd>
            <dt>标注 Tag</dt>
            <dd>
              {canReadTags &&
                ann &&
                (ann.task_options.length > 1 ||
                  ann.availability === "SELECTION_REQUIRED") && (
                  <Select
                    aria-label={title + "标注任务"}
                    placeholder="选择标注任务"
                    value={ann.task_id ?? undefined}
                    onChange={setTask}
                    options={ann.task_options.map((t) => ({
                      value: t.task_id,
                      label: t.task_id,
                    }))}
                  />
                )}
              {!canReadTags || ann?.availability === "FORBIDDEN" ? (
                "无标注读取权限"
              ) : ann?.availability === "SELECTION_REQUIRED" ? (
                "请选择标注任务"
              ) : !ann?.current_revision ? (
                "无已保存标注"
              ) : tags.isError ? (
                <Alert
                  type="warning"
                  title="Tag 读取失败"
                  description={tags.error.message}
                />
              ) : tags.isPending ? (
                "正在读取 Tag…"
              ) : labels.length ? (
                <Space wrap>
                  {labels.map((label) => (
                    <Tag key={label}>{label}</Tag>
                  ))}
                </Space>
              ) : (
                "此修订没有 Tag"
              )}
            </dd>
          </dl>
          {!playback && <p>此版本暂无可用的同步回放入口。</p>}
        </>
      )}
    </Card>
  );
}
