import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Input, Select, Space } from "antd";
import type { Scope } from "../../shared/scope/model";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import {
  loadCollectionAnnotationHistory,
  type AnnotationSelection,
  type PackageData,
} from "../../features/exports/package-deliveries";

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
  const [members, setMembers] = useState<string[]>([]);
  const [label, setLabel] = useState("任务分段");
  const [allowUnreviewed, setAllowUnreviewed] = useState(false);
  const [confirmation, setConfirmation] = useState<string | null>(null);
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
  const changeRef = useRef(onChange);
  useEffect(() => {
    changeRef.current = onChange;
  }, [onChange]);
  useEffect(() => {
    changeRef.current(null);
    setConfirmation(null);
    setMembers([]);
    setAllowUnreviewed(false);
  }, [chosenTask, selected]);
  const saved = history.data?.revisions.find((r) => r.revision === selected);
  const approved =
    selected !== undefined && selected === history.data?.task.approved_revision;
  const tags = (saved?.tags ?? [])
    .filter((t) => members.includes(t.annotation_id))
    .toSorted((a, b) => a.start_step - b.start_step);
  const overlap = tags.some(
    (t, i) => i > 0 && t.start_step < tags[i - 1]!.end_step,
  );
  function reset() {
    onChange(null);
    setConfirmation(null);
  }
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <strong>{data.identity.episode_id} · 使用当前保存的标注</strong>
      {(overview.error || history.error) && (
        <Alert
          type="error"
          title="标注读取失败"
          description={(overview.error ?? history.error)?.message}
        />
      )}
      {overview.data?.annotation.availability === "NOT_CREATED" && (
        <Alert type="warning" title="此数据版本尚无标注任务" />
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
            label: t.task_id,
          }))}
          onChange={(value) => {
            reset();
            setTaskId(value);
            setAllowUnreviewed(false);
            setMembers([]);
          }}
        />
      )}
      {history.data && !history.error && !overview.error && (
        <>
          <span>导出使用当前保存的标注，无需选择版本。</span>
          <Input
            aria-label={`分段轨道名称 ${data.identity.episode_id}`}
            value={label}
            onChange={(e) => {
              reset();
              setLabel(e.target.value);
            }}
          />
          <span>
            勾选组成任务分段轨道的标签；全部标签仍随包保留，未覆盖帧编号为 -1。
          </span>
          <Checkbox.Group
            value={members}
            onChange={(values) => {
              reset();
              setMembers(values as string[]);
            }}
            options={(saved?.tags ?? []).map((t) => ({
              value: t.annotation_id,
              label: `${t.label || t.tag_id} [${t.start_step}, ${t.end_step})`,
            }))}
          />
          {overlap && (
            <Alert type="error" title="选定轨道的区间重叠，请调整标签选择" />
          )}
          {!approved && (
            <Checkbox
              checked={allowUnreviewed}
              onChange={(e) => {
                reset();
                setAllowUnreviewed(e.target.checked);
              }}
            >
              包含尚未审核的当前标注
            </Checkbox>
          )}
          <Button
            disabled={
              !chosenTask ||
              selected === undefined ||
              !members.length ||
              !label.trim() ||
              overlap ||
              (!approved && !allowUnreviewed)
            }
            onClick={() => {
              if (!chosenTask || selected === undefined) return;
              onChange({
                task_id: chosenTask,
                revision: selected,
                allow_unreviewed: !approved && allowUnreviewed,
                track: {
                  format: "hc-subtask-track/1",
                  track_id: "task-segments",
                  label: label.trim(),
                  annotation_ids: [...members].sort(),
                },
              });
              setConfirmation(
                "已确认当前标注及分段；导出任务将保存本次使用的标注内容。",
              );
            }}
          >
            确认当前标注和分段
          </Button>
          {confirmation && <span role="status">{confirmation}</span>}
        </>
      )}
    </Space>
  );
}
