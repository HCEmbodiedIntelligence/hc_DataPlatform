import { ResourceLabel } from "../../shared/lib/resource-labels";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Select, Space, Tag } from "antd";
import type { Scope } from "../../shared/scope/model";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import {
  loadCollectionAnnotationHistory,
  type AnnotationSelection,
  type PackageData,
} from "../../features/exports/package-deliveries";
import styles from "./CollectionAnnotationChooser.module.css";

export function CollectionAnnotationChooser({
  scope,
  data,
  onChange,
}: {
  scope: Scope;
  data: PackageData;
  onChange: (value: AnnotationSelection | null) => void;
}) {
  const [taskId, setTaskId] = useState<string>();
  const [trackSelection, setTrackSelection] = useState<{
    key: string;
    members: string[];
  }>();
  const overview = useQuery({
    queryKey: ["batch-annotation-overview", scope, data],
    queryFn: ({ signal }) => loadEpisodeOverview(scope, data, {}, signal),
  });
  const chosenTask = taskId ?? overview.data?.annotation.task_id ?? undefined;
  const history = useQuery({
    queryKey: ["batch-annotation-history", scope, data, chosenTask],
    enabled: Boolean(chosenTask),
    queryFn: ({ signal }) =>
      loadCollectionAnnotationHistory(scope, data, chosenTask!, signal),
  });
  const selected = history.data?.task.current_revision;
  const selectionKey = JSON.stringify([chosenTask, selected]);
  const members = useMemo(
    () => (trackSelection?.key === selectionKey ? trackSelection.members : []),
    [trackSelection, selectionKey],
  );
  const changeRef = useRef(onChange);
  useEffect(() => {
    changeRef.current = onChange;
  }, [onChange]);
  const saved = history.data?.revisions.find((r) => r.revision === selected);
  const approved =
    selected !== undefined && selected === history.data?.task.approved_revision;
  const tags = (saved?.tags ?? [])
    .filter((t) => members.includes(t.annotation_id))
    .toSorted((a, b) => a.start_step - b.start_step);
  const overlap = tags.some(
    (t, i) => i > 0 && t.start_step < tags[i - 1]!.end_step,
  );
  const loading = overview.isFetching || history.isFetching;
  const ready = Boolean(
    chosenTask &&
      saved &&
      !overview.error &&
      !history.error &&
      !loading &&
      !overlap,
  );
  useEffect(() => {
    changeRef.current(
      ready && chosenTask && selected !== undefined
        ? {
            task_id: chosenTask,
            revision: selected,
            allow_unreviewed: !approved,
            track: {
              format: "hc-subtask-track/1",
              track_id: "task-segments",
              label: "任务分段",
              annotation_ids: [...members].sort(),
            },
          }
        : null,
    );
  }, [ready, chosenTask, selected, approved, members]);
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <div className={styles.heading}>
        <strong>
          <ResourceLabel
            kind="episode"
            id={data.identity.episode_id}
            datasetId={data.identity.dataset_id}
          />
        </strong>
        {saved && !loading && !overview.error && !history.error && (
          <span role="status">
            已保存 {saved.tags.length} 个标签{" "}
            <Tag color={approved ? "success" : "default"}>
              {approved ? "已审核" : "未审核 · 将按当前内容导出"}
            </Tag>
          </span>
        )}
      </div>
      {loading && <span role="status">正在读取已保存的标注…</span>}
      {(overview.error || history.error) && (
        <Alert
          type="error"
          title="标注读取失败"
          description={(overview.error ?? history.error)?.message}
          action={
            <Button
              size="small"
              onClick={() => {
                void overview.refetch();
                if (chosenTask) void history.refetch();
              }}
            >
              重新读取
            </Button>
          }
        />
      )}
      {overview.data?.annotation.availability === "NOT_CREATED" && (
        <Alert
          type="warning"
          title="此 Episode 尚无标注，请先保存标注，或选择不带标注的数据导出。"
        />
      )}
      {overview.data?.annotation.availability === "FORBIDDEN" && (
        <Alert type="warning" title="缺少标注读取权限" />
      )}
      {overview.data && overview.data.annotation.task_options.length > 1 && (
        <Select
          aria-label={`标注任务 ${data.identity.episode_id}`}
          value={chosenTask}
          placeholder="明确选择一个标注任务"
          style={{ width: "100%" }}
          options={overview.data.annotation.task_options.map((t) => ({
            value: t.task_id,
            label: <ResourceLabel kind="annotation_task" id={t.task_id} />,
          }))}
          onChange={setTaskId}
        />
      )}
      {history.data && !saved && !loading && !history.error && (
        <Alert
          type="warning"
          title="未找到当前已保存的标注，请重新读取。"
          action={
            <Button size="small" onClick={() => void history.refetch()}>
              重新读取
            </Button>
          }
        />
      )}
      {saved && !history.error && !overview.error && !loading && (
        <>
          {saved.tags.length > 0 && (
            <details className={styles.segments}>
              <summary>
                任务分段（可选）
                {members.length > 0 && ` · 已选 ${members.length} 段`}
              </summary>
              <p>
                全部标签都会导出。需要为数据帧添加动作分段编号时，再勾选对应标签；不选也可直接导出。
              </p>
              <Checkbox.Group
                className={styles.tags}
                aria-label={`任务分段 ${data.identity.episode_id}`}
                value={members}
                onChange={(values) =>
                  setTrackSelection({
                    key: selectionKey,
                    members: values as string[],
                  })
                }
                options={saved.tags.map((t) => ({
                  value: t.annotation_id,
                  label: `${t.label || t.tag_id} [${t.start_step}, ${t.end_step})`,
                }))}
              />
            </details>
          )}
          {overlap && (
            <Alert
              type="error"
              title="所选分段有重叠，请取消重叠标签，或清空分段直接导出。"
              action={
                <Button
                  size="small"
                  onClick={() => setTrackSelection(undefined)}
                >
                  清空分段
                </Button>
              }
            />
          )}
        </>
      )}
    </Space>
  );
}
