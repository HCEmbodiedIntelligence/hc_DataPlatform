import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Input, Segmented, Tag } from "antd";
import {
  ChevronLeft,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useEffect, useId, useRef } from "react";
import type {
  ContinuousRecording,
  RecordingGateway,
  RecordingScope,
} from "./api";
import { formatDuration, parseNanoseconds } from "./model";
import {
  recordingFileName,
  recordingRobotName,
  recordingTaskNumber,
  recordingSearchValues,
} from "./presentation";
import styles from "./styles.module.css";

export interface RecordingFilter {
  search: string;
  sameTask: boolean;
  stage: "all" | "pending" | "sliced";
}

export function RecordingNavigator({
  gateway,
  scope,
  scopeKey,
  recording,
  filter,
  onFilterChange,
  onSelect,
  disabled,
  collapsed,
  onToggle,
}: {
  gateway: RecordingGateway;
  scope: RecordingScope;
  scopeKey: string;
  recording: ContinuousRecording;
  filter: RecordingFilter;
  onFilterChange: (next: RecordingFilter) => void;
  onSelect: (id: string) => void;
  disabled: boolean;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const contentId = useId();
  const selectedRow = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    selectedRow.current?.scrollIntoView?.({ block: "nearest" });
  }, [recording.recording_id, collapsed]);
  const list = useQuery({
    queryKey: ["continuous-recordings", scopeKey],
    queryFn: ({ signal }) => gateway.list(scope, signal),
    retry: false,
    refetchInterval: 5_000,
  });
  const search = filter.search.trim().toLocaleLowerCase("zh-CN");
  // Detail reflects a just-saved draft/finalization before the list poll catches up.
  const recordings = [
    recording,
    ...(list.data?.items ?? []).filter(
      (item) => item.recording_id !== recording.recording_id,
    ),
  ]
    .filter(
      (item) =>
        !filter.sameTask ||
        item.collection_task_id === recording.collection_task_id,
    )
    .sort(
      (a, b) =>
        b.capture_started_at.localeCompare(a.capture_started_at) ||
        a.recording_id.localeCompare(b.recording_id),
    );
  const matches = recordings.filter(
    (item) =>
      (filter.stage === "all" ||
        (item.status === "SLICED") === (filter.stage === "sliced")) &&
      (!search ||
        recordingSearchValues(item).some((value) =>
          value.toLocaleLowerCase("zh-CN").includes(search),
        )),
  );
  // Keep the current source visible even after submitting it in the pending filter.
  const visible = matches.some(
    (item) => item.recording_id === recording.recording_id,
  )
    ? matches
    : [recording, ...matches];
  const index = visible.findIndex(
    (item) => item.recording_id === recording.recording_id,
  );
  const previous = visible[index - 1];
  const next = visible[index + 1];
  const nextPending = [
    ...visible.slice(index + 1),
    ...visible.slice(0, index),
  ].find((item) => item.status !== "SLICED");
  return (
    <aside
      className={styles.recordingNavigator}
      data-collapsed={collapsed}
      aria-label="原始录制切换"
      onKeyDown={(event) => event.stopPropagation()}
    >
      <div className={styles.navigatorHeading}>
        {!collapsed && <strong>原始录制</strong>}
        <Button
          type="text"
          size="small"
          icon={
            collapsed ? (
              <PanelLeftOpen size={17} />
            ) : (
              <PanelLeftClose size={17} />
            )
          }
          aria-label={collapsed ? "展开原始录制" : "收起原始录制"}
          title={collapsed ? "展开原始录制" : "收起原始录制"}
          aria-expanded={!collapsed}
          aria-controls={contentId}
          onClick={onToggle}
        />
      </div>
      <div
        id={contentId}
        hidden={collapsed}
        className={styles.navigatorContent}
      >
        <span className={styles.navigatorCount}>
          {recordings.length} 条 · 待分片{" "}
          {recordings.filter((item) => item.status !== "SLICED").length} 条
        </span>
        <Input.Search
          aria-label="搜索原始录制"
          placeholder="搜索录制"
          title="搜索文件名、机器人、任务编号"
          allowClear
          value={filter.search}
          onChange={(event) =>
            onFilterChange({ ...filter, search: event.target.value })
          }
        />
        <Checkbox
          checked={filter.sameTask}
          onChange={(event) =>
            onFilterChange({ ...filter, sameTask: event.target.checked })
          }
        >
          仅当前采集任务
        </Checkbox>
        <Segmented<RecordingFilter["stage"]>
          block
          aria-label="原始录制状态"
          value={filter.stage}
          options={[
            { label: "全部", value: "all" },
            { label: "待分片", value: "pending" },
            { label: "已成片", value: "sliced" },
          ]}
          onChange={(stage) => onFilterChange({ ...filter, stage })}
        />
        <div className={styles.navigatorControls}>
          <Button
            icon={<ChevronLeft size={15} />}
            disabled={disabled || !previous}
            onClick={() => previous && onSelect(previous.recording_id)}
          >
            上一条
          </Button>
          <Button
            disabled={disabled || !next}
            onClick={() => next && onSelect(next.recording_id)}
          >
            下一条 <ChevronRight size={15} />
          </Button>
        </div>
        <Button
          disabled={disabled || !nextPending}
          onClick={() => nextPending && onSelect(nextPending.recording_id)}
        >
          下一条待分片
        </Button>
        {list.isPending && <p role="status">正在读取录制列表…</p>}
        {list.isError && (
          <Alert
            type="warning"
            title="录制列表读取失败"
            action={
              <Button size="small" onClick={() => void list.refetch()}>
                重试
              </Button>
            }
          />
        )}
        <div className={styles.navigatorItems}>
          {visible.map((item) => (
            <button
              key={item.recording_id}
              ref={
                item.recording_id === recording.recording_id
                  ? selectedRow
                  : undefined
              }
              type="button"
              className={styles.navigatorItem}
              title={recordingFileName(item)}
              aria-current={
                item.recording_id === recording.recording_id
                  ? "page"
                  : undefined
              }
              aria-label={`切换到录制 ${recordingFileName(item)}`}
              disabled={disabled}
              onClick={() => onSelect(item.recording_id)}
            >
              <span className={styles.navigatorItemHeading}>
                <time dateTime={item.capture_started_at}>
                  {new Date(item.capture_started_at).toLocaleString("zh-CN", {
                    hour12: false,
                  })}
                </time>
                <Tag
                  color={
                    item.status === "SLICED"
                      ? "success"
                      : item.current_revision > 0
                        ? "warning"
                        : "blue"
                  }
                >
                  {item.status === "SLICED"
                    ? "已成片"
                    : item.current_revision > 0
                      ? "草稿"
                      : "待分片"}
                </Tag>
              </span>
              <strong title={recordingFileName(item)}>
                {recordingFileName(item)}
              </strong>
              <span>
                {recordingRobotName(item)} · 任务 {recordingTaskNumber(item)} ·{" "}
                {formatDuration(parseNanoseconds(item.duration_ns))}
              </span>
            </button>
          ))}
          {matches.length === 0 && !list.isPending && !list.isError && (
            <p>没有符合条件的录制，当前录制保留显示。</p>
          )}
        </div>
      </div>
    </aside>
  );
}
