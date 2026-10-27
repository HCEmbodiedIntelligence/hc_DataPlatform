import { useEffect, useRef, useState } from "react";
import { Alert, Button, Drawer, Input, InputNumber, Tag, Tooltip } from "antd";
import { Eye, Pencil, Trash2 } from "lucide-react";
import { TaskDescriptionLabels } from "./TaskDescriptionLabels";
import {
  clampBoundary,
  formatTimecode,
  nsToSeconds,
  secondsToNs,
  type EditableSlice,
  type SliceValidationIssue,
} from "./model";
import styles from "./styles.module.css";

export function EpisodeSliceList({
  slices,
  selectedId,
  readOnly,
  durationNs,
  minimumDurationNs,
  issues,
  hasVideo,
  onSelect,
  onChange,
  onDelete,
  onPreview,
}: {
  readonly slices: readonly EditableSlice[];
  readonly selectedId: string | null;
  readonly readOnly: boolean;
  readonly durationNs: number;
  readonly minimumDurationNs: number;
  readonly issues: readonly SliceValidationIssue[];
  readonly hasVideo: boolean;
  readonly onSelect: (slice: EditableSlice) => void;
  readonly onChange: (slices: readonly EditableSlice[]) => void;
  readonly onDelete: (episodeId: string) => void;
  readonly onPreview: (slice: EditableSlice) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const editingSlice = slices.find((slice) => slice.episodeId === editingId);
  const editingIssue = issues.find((issue) => issue.episodeId === editingId);
  const updateSlice = (patch: Partial<EditableSlice>) =>
    onChange(
      slices.map((slice) =>
        slice.episodeId === editingId ? { ...slice, ...patch } : slice,
      ),
    );
  const deleteSlice = (episodeId: string) => {
    onDelete(episodeId);
    if (episodeId === editingId) setEditingId(null);
  };

  useEffect(() => {
    const list = listRef.current;
    const selected = list?.querySelector<HTMLElement>('[data-selected="true"]');
    if (!list || !selected) return;
    const bounds = list.getBoundingClientRect();
    const row = selected.getBoundingClientRect();
    if (row.top < bounds.top) list.scrollTop += row.top - bounds.top;
    else if (row.bottom > bounds.bottom)
      list.scrollTop += row.bottom - bounds.bottom;
  }, [selectedId, slices.length]);

  return (
    <>
      <aside
        className={styles.episodeInspector}
        aria-label="Episode 列表与属性"
      >
        <div className={styles.episodeList} ref={listRef}>
          {slices.length === 0 ? (
            <div className={styles.emptyEpisodes}>
              <strong>还没有 Episode</strong>
              <span>在时间轴拖拽新增，或直接提交完整录制。</span>
            </div>
          ) : (
            slices.map((slice, index) => {
              const issue = issues.find(
                (item) => item.episodeId === slice.episodeId,
              );
              return (
                <article
                  className={styles.episodeCard}
                  data-selected={selectedId === slice.episodeId || undefined}
                  key={slice.episodeId}
                >
                  <button
                    type="button"
                    className={styles.episodeCardHeader}
                    onClick={() => onSelect(slice)}
                    aria-pressed={selectedId === slice.episodeId}
                  >
                    <span className={styles.episodeOrdinal}>
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <strong title={slice.title || slice.episodeId}>
                      {slice.title || slice.episodeId}
                    </strong>
                    <small>
                      {formatTimecode(slice.startNs)} —{" "}
                      {formatTimecode(slice.endNs)}
                    </small>
                    {issue ? <Tag color="error">边界错误</Tag> : null}
                  </button>
                  <div className={styles.episodeRowActions}>
                    <Tooltip title={hasVideo ? "预览片段" : "定位片段"}>
                      <Button
                        size="small"
                        type="text"
                        icon={<Eye size={14} />}
                        aria-label={`预览 Episode ${index + 1}`}
                        onClick={() => onPreview(slice)}
                      >
                        <span className={styles.episodeActionText}>预览</span>
                      </Button>
                    </Tooltip>
                    <Button
                      size="small"
                      type="text"
                      icon={<Pencil size={14} />}
                      aria-label={`编辑 Episode ${index + 1}`}
                      onClick={() => {
                        onSelect(slice);
                        setEditingId(slice.episodeId);
                      }}
                    >
                      <span className={styles.episodeActionText}>
                        {readOnly ? "详情" : "编辑"}
                      </span>
                    </Button>
                    <Tooltip title="删除片段">
                      <Button
                        size="small"
                        danger
                        type="text"
                        icon={<Trash2 size={14} />}
                        aria-label={`删除 Episode ${index + 1}`}
                        disabled={readOnly}
                        onClick={() => deleteSlice(slice.episodeId)}
                      />
                    </Tooltip>
                  </div>
                </article>
              );
            })
          )}
        </div>
      </aside>
      <Drawer
        title={editingSlice
          ? `Episode ${String(slices.findIndex((slice) => slice.episodeId === editingId) + 1).padStart(2, "0")} 属性`
          : "Episode 属性"}
        size={420}
        styles={{ wrapper: { maxWidth: "100vw" }, body: { padding: 16 } }}
        open={Boolean(editingSlice)}
        onClose={() => setEditingId(null)}
        destroyOnHidden
        extra={
          editingSlice && (
            <Button
              danger
              type="text"
              size="small"
              icon={<Trash2 size={14} />}
              disabled={readOnly}
              onClick={() => deleteSlice(editingSlice.episodeId)}
            >
              删除此片段
            </Button>
          )
        }
        footer={
          <Button block onClick={() => setEditingId(null)}>
            返回切片
          </Button>
        }
      >
        {editingSlice && (
          <div
            className={styles.episodeFields}
            onKeyDown={(event) => {
              if (event.key !== "Escape") event.stopPropagation();
            }}
          >
            <label>
              标题
              <Input
                value={editingSlice.title}
                disabled={readOnly}
                maxLength={256}
                onChange={(event) => updateSlice({ title: event.target.value })}
              />
            </label>
            <div className={styles.boundaryFields}>
              <label>
                开始（秒）
                <InputNumber
                  aria-label="开始（秒）"
                  value={Number(nsToSeconds(editingSlice.startNs).toFixed(3))}
                  disabled={readOnly}
                  min={0}
                  max={Number(nsToSeconds(editingSlice.endNs).toFixed(3))}
                  step={0.033}
                  onChange={(value) =>
                    typeof value === "number" &&
                    onChange(
                      clampBoundary(
                        slices,
                        editingSlice.episodeId,
                        "start",
                        secondsToNs(value),
                        durationNs,
                        minimumDurationNs,
                      ),
                    )
                  }
                />
              </label>
              <label>
                结束（秒）
                <InputNumber
                  aria-label="结束（秒）"
                  value={Number(nsToSeconds(editingSlice.endNs).toFixed(3))}
                  disabled={readOnly}
                  min={Number(nsToSeconds(editingSlice.startNs).toFixed(3))}
                  max={Number(nsToSeconds(durationNs).toFixed(3))}
                  step={0.033}
                  onChange={(value) =>
                    typeof value === "number" &&
                    onChange(
                      clampBoundary(
                        slices,
                        editingSlice.episodeId,
                        "end",
                        secondsToNs(value),
                        durationNs,
                        minimumDurationNs,
                      ),
                    )
                  }
                />
              </label>
            </div>
            {editingIssue && (
              <Alert type="error" title={editingIssue.message} showIcon />
            )}
            <TaskDescriptionLabels
              labels={editingSlice.taskLabels}
              readOnly={readOnly}
              onChange={(taskLabels) => updateSlice({ taskLabels })}
            />
            <label>
              备注
              <Input.TextArea
                value={editingSlice.notes}
                disabled={readOnly}
                autoSize={{ minRows: 2, maxRows: 5 }}
                maxLength={4096}
                onChange={(event) => updateSlice({ notes: event.target.value })}
              />
            </label>
          </div>
        )}
      </Drawer>
    </>
  );
}
