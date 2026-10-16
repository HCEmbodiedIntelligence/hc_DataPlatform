import { Button, Checkbox, Input, Segmented, Tag } from "antd";
import {
  ChevronLeft,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useEffect, useId, useRef } from "react";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationTask,
} from "../../features/annotation/runtime/runtime-annotation-adapter";
import { annotationTaskStatusLabel } from "../../features/annotation/runtime/runtime-annotation-adapter";
import type { WorkbenchCollectionItem } from "../../features/viewer/workbench-contract";
import styles from "./AnnotationNavigator.module.css";

export interface AnnotationNavigatorPreferences {
  readonly collapsed: boolean;
  readonly search: string;
  readonly sameDataset: boolean;
  readonly stage: "all" | "pending";
}

export const defaultAnnotationNavigatorPreferences: AnnotationNavigatorPreferences =
  {
    collapsed: false,
    search: "",
    sameDataset: true,
    stage: "all",
  };

export function AnnotationNavigator({
  task,
  tasks,
  items,
  mode,
  principalId,
  preferences,
  onChange,
  onSelect,
  disabled,
}: {
  readonly task: RuntimeAnnotationTask;
  readonly tasks: readonly RuntimeAnnotationTask[];
  readonly items: readonly WorkbenchCollectionItem[];
  readonly mode: AnnotationWorkbenchMode;
  readonly principalId?: string;
  readonly preferences: AnnotationNavigatorPreferences;
  readonly onChange: (next: AnnotationNavigatorPreferences) => void;
  readonly onSelect: (id: string) => void;
  readonly disabled: boolean;
}) {
  const contentId = useId();
  const selectedRow = useRef<HTMLButtonElement>(null);
  const { collapsed, search, sameDataset, stage } = preferences;
  useEffect(() => {
    selectedRow.current?.scrollIntoView?.({ block: "nearest" });
  }, [task.task_id, collapsed]);
  const review = mode === "tag-review";
  const pendingLabel = review ? "待审核" : "待标注";
  const isPending = (item: RuntimeAnnotationTask) =>
    review
      ? item.status === "SUBMITTED"
      : (item.status === "DRAFT" || item.status === "NEEDS_REVISION") &&
        (!item.assignee_id || !principalId || item.assignee_id === principalId);
  const labels = new Map(items.map((item) => [item.id, item]));
  // Use the fresh task detail after saving/submitting; keep ordering stable across edits.
  const scoped = [
    task,
    ...tasks.filter((item) => item.task_id !== task.task_id),
  ]
    .filter((item) => !sameDataset || item.dataset_id === task.dataset_id)
    .sort(
      (a, b) =>
        (a.created_at ?? "").localeCompare(b.created_at ?? "") ||
        a.task_id.localeCompare(b.task_id, "zh-CN", { numeric: true }),
    );
  const query = search.trim().toLocaleLowerCase("zh-CN");
  const matches = scoped.filter((item) => {
    const label = labels.get(item.task_id);
    return (
      (stage === "all" || isPending(item)) &&
      (!query ||
        [label?.label, label?.description].some((value) =>
          value?.toLocaleLowerCase("zh-CN").includes(query),
        ))
    );
  });
  const visible = matches.some((item) => item.task_id === task.task_id)
    ? matches
    : [task, ...matches];
  const index = visible.findIndex((item) => item.task_id === task.task_id);
  const previous = visible[index - 1];
  const next = visible[index + 1];
  const nextPending = [
    ...visible.slice(index + 1),
    ...visible.slice(0, index),
  ].find(isPending);
  return (
    <aside
      className={styles.navigator}
      data-collapsed={collapsed}
      aria-label="标注数据切换"
      onKeyDown={(event) => event.stopPropagation()}
    >
      <div className={styles.heading}>
        {!collapsed && <strong>标注数据</strong>}
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
          aria-label={collapsed ? "展开标注数据" : "收起标注数据"}
          title={collapsed ? "展开标注数据" : "收起标注数据"}
          aria-expanded={!collapsed}
          aria-controls={contentId}
          onClick={() => onChange({ ...preferences, collapsed: !collapsed })}
        />
      </div>
      <div id={contentId} hidden={collapsed} className={styles.content}>
        <span className={styles.count}>
          {scoped.length} 条 · {pendingLabel} {scoped.filter(isPending).length}{" "}
          条
        </span>
        <Input
          size="small"
          allowClear
          aria-label="搜索标注数据"
          placeholder="搜索数据、Episode"
          value={search}
          onChange={(event) =>
            onChange({ ...preferences, search: event.target.value })
          }
        />
        <Checkbox
          checked={sameDataset}
          onChange={(event) =>
            onChange({ ...preferences, sameDataset: event.target.checked })
          }
        >
          仅当前数据集
        </Checkbox>
        <Segmented<AnnotationNavigatorPreferences["stage"]>
          size="small"
          block
          aria-label="标注数据状态"
          value={stage}
          options={[
            { label: "全部", value: "all" },
            { label: pendingLabel, value: "pending" },
          ]}
          onChange={(value) => onChange({ ...preferences, stage: value })}
        />
        <div className={styles.controls}>
          <Button
            size="small"
            icon={<ChevronLeft size={14} />}
            disabled={disabled || !previous}
            onClick={() => previous && onSelect(previous.task_id)}
          >
            上一条
          </Button>
          <Button
            size="small"
            disabled={disabled || !next}
            onClick={() => next && onSelect(next.task_id)}
          >
            下一条 <ChevronRight size={14} />
          </Button>
        </div>
        <Button
          size="small"
          disabled={disabled || !nextPending}
          onClick={() => nextPending && onSelect(nextPending.task_id)}
        >
          下一条{pendingLabel}
        </Button>
        <div className={styles.items}>
          {visible.map((item) => {
            const label = labels.get(item.task_id);
            return (
              <button
                key={item.task_id}
                ref={item.task_id === task.task_id ? selectedRow : undefined}
                type="button"
                className={styles.item}
                aria-current={
                  item.task_id === task.task_id ? "page" : undefined
                }
                aria-label={`切换到标注数据 ${label?.label ?? "Episode"}`}
                disabled={disabled}
                onClick={() => onSelect(item.task_id)}
              >
                <Tag
                  color={
                    item.status === "APPROVED"
                      ? "success"
                      : item.status === "REJECTED"
                        ? "error"
                        : item.status === "SUBMITTED" ||
                            item.status === "NEEDS_REVISION"
                          ? "warning"
                          : "blue"
                  }
                >
                  {annotationTaskStatusLabel(item.status)}
                </Tag>
                <strong title={label?.label}>
                  {label?.label ?? "Episode"}
                </strong>
                <small title={label?.description}>{label?.description}</small>
              </button>
            );
          })}
          {matches.length === 0 && (
            <p className={styles.empty} role="status">
              没有符合条件的数据，当前条目保留显示。
            </p>
          )}
        </div>
      </div>
    </aside>
  );
}
