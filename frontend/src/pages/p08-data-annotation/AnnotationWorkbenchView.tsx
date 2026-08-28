import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  JSX,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  RefObject,
} from "react";
import { Alert, Form, Input, Modal, Select, Tooltip } from "antd";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleAlert,
  Database,
  Eye,
  FilePlus2,
  GitCompareArrows,
  Info,
  ListTree,
  LoaderCircle,
  LockKeyhole,
  PencilLine,
  Plus,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  Tags,
  Wrench,
  X,
} from "lucide-react";
import {
  DataVisualizationWorkbench,
  ViewerJointAngleCurvePanel,
  ViewerRobotPosePanel,
  WorkbenchCollectionPanel,
} from "../../features/viewer";
import type {
  DomainError,
  RobotSceneCoreProps,
  StreamDescriptor,
  ViewerPanelRenderer,
  ViewerTimelineSelection,
} from "../../features/viewer";
import { createPlaybackClock } from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import type {
  ManualIssueSeverity,
  ManualIssueType,
} from "../../entities/manual-issue";
import { DangerousActionDialog } from "../../features/annotation";
import { EntityDrawer } from "../../shared/ui/layout/EntityDrawer";
import {
  buildRuntimeCameraStreams,
  buildRuntimeWorkbenchAdapter,
  createClientMutationId,
  normalizeStepRateHz,
  resolveOriginalRevision,
  resolveReviewRevision,
  resolveReviewSubmission,
  stepToTimelineNs,
  timelineNsToStep,
} from "./runtime-annotation-adapter";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationBundle,
  RuntimeAnnotationScope,
  RuntimeAnnotationTag,
  RuntimeReviewDecision,
} from "./runtime-annotation-adapter";
import { buildTagSchemaIndex, evaluateAnnotationTags } from "./tag-validation";
import type { TagReviewCheckResult } from "./tag-validation";
import { buildRuntimeJointAngleStream } from "./joint-angle-stream";
import "./p08.css";
import styles from "./workbench.module.css";

export interface AnnotationWorkbenchPermissions {
  readonly hasAnnotationDraft: boolean;
  readonly canCreate: boolean;
  readonly canEdit: boolean;
  readonly canSave: boolean;
  readonly canSubmit: boolean;
  readonly canReview: boolean;
  readonly canRevise: boolean;
  readonly readOnlyReason?: string;
  readonly createUnavailableReason?: string;
  readonly annotationUnavailableReason?: string;
  readonly reviewUnavailableReason?: string;
  readonly revisionUnavailableReason?: string;
}

export interface AnnotationWorkbenchViewProps {
  readonly bundle: RuntimeAnnotationBundle;
  readonly scope: RuntimeAnnotationScope;
  readonly mode: AnnotationWorkbenchMode;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly dirty: boolean;
  readonly permissions: AnnotationWorkbenchPermissions;
  readonly recoveryAvailable?: boolean;
  readonly renderPanel?: ViewerPanelRenderer;
  readonly robotScene?: Omit<RobotSceneCoreProps, "clock">;
  readonly robotSceneUnavailableReason?: string;
  readonly jointAngleStream?: StreamDescriptor;
  readonly autoAnnotationPanel?: ReactNode;
  readonly externalError?: unknown;
  readonly canReportDataIssue?: boolean;
  readonly reportDataIssueUnavailableReason?: string;
  readonly onTagsChange: (tags: readonly RuntimeAnnotationTag[]) => void;
  readonly onCreateAnnotation?: () => Promise<void>;
  readonly onDiscardChanges?: () => void;
  readonly onSave: () => Promise<void>;
  readonly onPreSubmitCheck?: () => Promise<readonly TagReviewCheckResult[]>;
  readonly onSubmit: () => Promise<void>;
  readonly onRestoreRevision?: (targetRevision: number) => Promise<void>;
  readonly onReview: (
    decision: RuntimeReviewDecision,
    comment: string,
  ) => Promise<void>;
  readonly onRecover?: () => void;
  readonly onDiscardRecovery?: () => void;
  readonly onOpenRevisions?: () => void;
  readonly onSelectTask?: (taskId: string) => void;
  readonly onSwitchMode?: (mode: AnnotationWorkbenchMode) => void;
  readonly onReportDataIssue?: (input: DataIssueReportInput) => Promise<void>;
}

export interface DataIssueReportInput {
  readonly streamRef: string;
  readonly relativeStartNs: string;
  readonly relativeEndNs: string;
  readonly issueType: ManualIssueType;
  readonly severity: ManualIssueSeverity;
  readonly note: string;
}

interface StepSelection {
  readonly startStep: number;
  readonly endStep: number;
}

type PendingAction =
  | "create"
  | "save"
  | "precheck"
  | "submit"
  | "restore"
  | RuntimeReviewDecision
  | null;
type DialogDecision =
  | "SAVE"
  | "SUBMIT"
  | "RESTORE"
  | RuntimeReviewDecision
  | null;
export type AnnotationWorkspaceMode =
  | "view"
  | "annotation"
  | "tag-review"
  | "revisions";
type PreSubmitCheckState =
  | { readonly status: "idle" }
  | {
      readonly status: "checking";
      readonly taskId: string;
      readonly tags: readonly RuntimeAnnotationTag[];
    }
  | {
      readonly status: "failed";
      readonly taskId: string;
      readonly tags: readonly RuntimeAnnotationTag[];
      readonly checks: readonly TagReviewCheckResult[];
    }
  | {
      readonly status: "passed";
      readonly taskId: string;
      readonly tags: readonly RuntimeAnnotationTag[];
      readonly checks: readonly TagReviewCheckResult[];
    }
  | {
      readonly status: "error";
      readonly taskId: string;
      readonly tags: readonly RuntimeAnnotationTag[];
    };

const idlePreSubmitCheck = { status: "idle" } as const;

const reviewDecisionLabels: Readonly<Record<RuntimeReviewDecision, string>> = {
  APPROVE: "审核通过",
  NEEDS_REVISION: "要求修改",
  REJECT: "拒绝",
};

const dataIssueTypeOptions: readonly {
  readonly value: ManualIssueType;
  readonly label: string;
}[] = [
  { value: "MISSING_FRAME", label: "画面缺帧" },
  { value: "STREAM_GAP", label: "数据流中断" },
  { value: "TIMESTAMP_DRIFT", label: "时间偏移" },
  { value: "POSE_JITTER", label: "姿态抖动" },
  { value: "CALIBRATION_MISMATCH", label: "标定不匹配" },
  { value: "INVALID_MASK", label: "无效区间" },
  { value: "OTHER", label: "其他数据问题" },
];

const dataIssueSeverityOptions: readonly {
  readonly value: ManualIssueSeverity;
  readonly label: string;
}[] = [
  { value: "LOW", label: "低" },
  { value: "MEDIUM", label: "中" },
  { value: "HIGH", label: "高" },
  { value: "CRITICAL", label: "紧急" },
];

function displayRevisionTime(value: string | undefined): string {
  if (!value) return "时间未返回";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function tagIdentity(prefix = "tag"): string {
  return createClientMutationId(prefix).replaceAll(":", "-");
}

function PathCrumbs({
  path,
}: {
  readonly path: readonly string[];
}): JSX.Element {
  return (
    <span
      className={styles.pathCrumbs}
      aria-label={`标签路径：${path.join("，")}`}
    >
      {path.map((part, index) => (
        <span key={`${part}:${index}`}>
          {index ? <ChevronRight aria-hidden="true" size={12} /> : null}
          <b>{part}</b>
        </span>
      ))}
    </span>
  );
}

function CheckList({
  checks,
}: {
  readonly checks: readonly TagReviewCheckResult[];
}): JSX.Element {
  return (
    <ol className={styles.checkList} aria-label="Tag 六项检查">
      {checks.map((check) => (
        <li key={check.kind} data-status={check.status}>
          <span className={styles.checkIcon}>
            {check.status === "PASS" ? (
              <Check aria-hidden="true" size={13} />
            ) : (
              <X aria-hidden="true" size={13} />
            )}
          </span>
          <span>
            <strong>{check.label}</strong>
            <small>{check.evidence}</small>
          </span>
          <em>{check.status === "PASS" ? "通过" : "阻断"}</em>
        </li>
      ))}
    </ol>
  );
}

interface IntervalTagRow {
  readonly tag: RuntimeAnnotationTag;
  readonly depth: number;
  readonly displayPath: readonly string[];
  readonly hasChildren: boolean;
}

function buildReviewComment(
  issueRows: readonly IntervalTagRow[],
  reviewerComment: string,
): string {
  const note = reviewerComment.trim();
  if (!issueRows.length) return note;
  const issueLines = issueRows.map((row, index) => {
    const path = row.displayPath.join(" / ").slice(0, 300);
    const annotationId = row.tag.annotation_id.slice(0, 200);
    return `${index + 1}. ${path} · 步区间 [${row.tag.start_step}, ${row.tag.end_step}) · 标注 ID ${annotationId}`;
  });
  return [
    `标注问题：${issueRows.length} 处`,
    ...issueLines,
    ...(note ? ["", `补充说明：${note}`] : []),
  ]
    .join("\n")
    .slice(0, 10_000);
}

function bundleFrequencyHz(bundle: RuntimeAnnotationBundle): number {
  return normalizeStepRateHz(bundle.datasetVersion.frequency_hz);
}

function stepTime(step: number, frequencyHz: number): string {
  return `${(Math.max(0, step) / frequencyHz).toFixed(2)}s`;
}

function stepRange(
  startStep: number,
  endStep: number,
  frequencyHz: number,
): string {
  return `${stepTime(startStep, frequencyHz)} – ${stepTime(endStep, frequencyHz)}`;
}

function intervalTagLabel(
  tag: RuntimeAnnotationTag,
  schemaIndex: ReturnType<typeof buildTagSchemaIndex>,
): string {
  return (
    tag.label?.trim() ||
    schemaIndex.byId.get(tag.tag_id)?.node.display_name ||
    tag.path.at(-1) ||
    tag.tag_id
  );
}

function intervalHierarchyRows(
  tags: readonly RuntimeAnnotationTag[],
  schemaIndex: ReturnType<typeof buildTagSchemaIndex>,
): readonly IntervalTagRow[] {
  const byId = new Map(tags.map((tag) => [tag.annotation_id, tag]));
  const children = new Map<string | null, RuntimeAnnotationTag[]>();
  for (const tag of tags) {
    const parentId =
      tag.parent_annotation_id && byId.has(tag.parent_annotation_id)
        ? tag.parent_annotation_id
        : null;
    const siblings = children.get(parentId) ?? [];
    siblings.push(tag);
    children.set(parentId, siblings);
  }
  for (const siblings of children.values())
    siblings.sort(
      (left, right) =>
        left.start_step - right.start_step ||
        left.end_step - right.end_step ||
        intervalTagLabel(left, schemaIndex).localeCompare(
          intervalTagLabel(right, schemaIndex),
          "zh-CN",
        ),
    );

  const rows: IntervalTagRow[] = [];
  const visited = new Set<string>();
  const visit = (
    tag: RuntimeAnnotationTag,
    depth: number,
    parentDisplayPath: readonly string[],
  ) => {
    if (visited.has(tag.annotation_id)) return;
    visited.add(tag.annotation_id);
    const label = intervalTagLabel(tag, schemaIndex);
    const displayPath = tag.parent_annotation_id
      ? [...parentDisplayPath, label]
      : tag.label
        ? [label]
        : (schemaIndex.byId.get(tag.tag_id)?.displayPath ?? [label]);
    const childTags = children.get(tag.annotation_id) ?? [];
    rows.push({
      tag,
      depth,
      displayPath,
      hasChildren: childTags.length > 0,
    });
    for (const child of childTags) visit(child, depth + 1, displayPath);
  };
  for (const root of children.get(null) ?? []) visit(root, 0, []);
  for (const tag of tags) visit(tag, 0, []);
  return rows;
}

function automaticParentRow(
  rows: readonly IntervalTagRow[],
  selection: StepSelection | null,
): IntervalTagRow | null {
  if (!selection) return null;
  return (
    rows
      .filter(
        ({ tag }) =>
          tag.start_step <= selection.startStep &&
          tag.end_step >= selection.endStep &&
          (tag.start_step < selection.startStep ||
            tag.end_step > selection.endStep),
      )
      .toSorted((left, right) => {
        const leftSpan = left.tag.end_step - left.tag.start_step;
        const rightSpan = right.tag.end_step - right.tag.start_step;
        return leftSpan - rightSpan || right.depth - left.depth;
      })[0] ?? null
  );
}

function TagEditorInspector(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly selection: StepSelection | null;
  readonly disabled: boolean;
  readonly onChange: (tags: readonly RuntimeAnnotationTag[]) => void;
}): JSX.Element {
  const frequencyHz = bundleFrequencyHz(props.bundle);
  const index = useMemo(
    () => buildTagSchemaIndex(props.bundle.schema),
    [props.bundle.schema],
  );
  const rows = useMemo(
    () => intervalHierarchyRows(props.tags, index),
    [index, props.tags],
  );
  const [newLabel, setNewLabel] = useState("");
  const parentRow = useMemo(
    () => automaticParentRow(rows, props.selection),
    [props.selection, rows],
  );
  const selectionProblem = (() => {
    if (!props.selection) return "先在下方共享时间轴拖选一个视频片段。";
    return null;
  })();
  const addInterval = () => {
    const label = newLabel.trim();
    if (!label || !props.selection || selectionProblem || props.disabled)
      return;
    const boundary = Math.max(1, props.bundle.task.base_step_count ?? 1);
    const startStep = Math.max(
      0,
      Math.min(props.selection.startStep, boundary - 1),
    );
    const endStep = Math.max(
      startStep + 1,
      Math.min(props.selection.endStep, boundary),
    );
    const annotationId = tagIdentity("tag");
    const tagId = tagIdentity("manual-tag");
    const next: RuntimeAnnotationTag = {
      annotation_id: annotationId,
      tag_id: tagId,
      label,
      parent_annotation_id: parentRow?.tag.annotation_id ?? null,
      path: [...(parentRow?.tag.path ?? []), tagId],
      start_step: startStep,
      end_step: endStep,
      attributes: {},
      relations: [],
      subject: null,
    };
    props.onChange([...props.tags, next]);
    setNewLabel("");
  };

  return (
    <section
      className={styles.timelineTagEditor}
      aria-labelledby="tag-editor-title"
    >
      <header className={styles.timelineTagEditorHeader}>
        <span className={styles.timelineTagEditorIcon}>
          <ListTree aria-hidden="true" size={15} />
        </span>
        <div className={styles.timelineTagEditorTitle}>
          <h2 id="tag-editor-title">多级 Tag</h2>
          <small>拖选后自动分级</small>
        </div>
        <label className={styles.compactTagField} htmlFor="manual-tag-label">
          <span>Tag 名称 *</span>
          <input
            autoComplete="off"
            disabled={props.disabled}
            id="manual-tag-label"
            maxLength={256}
            name="manual-tag-label"
            placeholder="输入标签，按 Enter 创建"
            value={newLabel}
            onChange={(event) => setNewLabel(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") addInterval();
            }}
          />
        </label>
        <div
          className={styles.compactSelectedRange}
          data-invalid={Boolean(selectionProblem) || undefined}
          role="status"
        >
          <span>当前选区</span>
          <strong>
            {props.selection
              ? `${props.selection.startStep}–${props.selection.endStep} 步`
              : "拖动时间轴选择"}
          </strong>
          <small>
            {props.selection
              ? parentRow
                ? `自动 L${parentRow.depth + 2} · 父级 ${intervalTagLabel(parentRow.tag, index)} · ${stepRange(props.selection.startStep, props.selection.endStep, frequencyHz)}`
                : `自动 L1 · ${stepRange(props.selection.startStep, props.selection.endStep, frequencyHz)}`
              : "尚未选择"}
          </small>
        </div>
        <button
          className={styles.addTagButton}
          disabled={
            !newLabel.trim() || Boolean(selectionProblem) || props.disabled
          }
          type="button"
          onClick={addInterval}
          aria-label="创建 Tag"
        >
          <Plus aria-hidden="true" size={14} />
          创建
        </button>
      </header>
    </section>
  );
}

function useCurrentStep(
  clock: ReturnType<typeof createPlaybackClock>,
  frequencyHz: number,
): number {
  const [step, setStep] = useState(() =>
    timelineNsToStep(clock.currentNs(), frequencyHz),
  );
  useEffect(
    () =>
      clock.subscribe((value) => setStep(timelineNsToStep(value, frequencyHz))),
    [clock, frequencyHz],
  );
  return step;
}

function ReviewInspector(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly clock: ReturnType<typeof createPlaybackClock>;
  readonly comment: string;
  readonly selectedIssueIds: ReadonlySet<string>;
  readonly onCommentChange: (value: string) => void;
  readonly onToggleIssue: (annotationId: string) => void;
}): JSX.Element {
  const frequencyHz = bundleFrequencyHz(props.bundle);
  const currentStep = useCurrentStep(props.clock, frequencyHz);
  const schemaIndex = useMemo(
    () => buildTagSchemaIndex(props.bundle.schema),
    [props.bundle.schema],
  );
  const checks = evaluateAnnotationTags({
    task: props.bundle.task,
    schema: props.bundle.schema,
    tags: props.tags,
  });
  const intervalRows = useMemo(
    () => intervalHierarchyRows(props.tags, schemaIndex),
    [props.tags, schemaIndex],
  );
  const currentTag =
    intervalRows
      .filter(
        (row) =>
          row.tag.start_step <= currentStep && currentStep < row.tag.end_step,
      )
      .toSorted(
        (left, right) =>
          right.depth - left.depth ||
          left.tag.end_step -
            left.tag.start_step -
            (right.tag.end_step - right.tag.start_step),
      )[0]?.tag ??
    props.tags[0] ??
    null;
  const currentRow = currentTag
    ? (intervalRows.find(
        (row) => row.tag.annotation_id === currentTag.annotation_id,
      ) ?? null)
    : null;
  const original = resolveOriginalRevision(props.bundle);
  const revised = resolveReviewRevision(props.bundle);
  const originalTag = currentTag
    ? (original?.tags.find(
        (tag) => tag.annotation_id === currentTag.annotation_id,
      ) ?? null)
    : null;
  const originalRow = originalTag
    ? (intervalHierarchyRows(original?.tags ?? [], schemaIndex).find(
        (row) => row.tag.annotation_id === originalTag.annotation_id,
      ) ?? null)
    : null;
  const serverSubmission = resolveReviewSubmission(props.bundle);
  const serverChecks = new Map(
    serverSubmission?.checks.map((check) => [check.kind, check.evidence]) ?? [],
  );

  return (
    <section
      className={styles.inspectorPanel}
      aria-labelledby="tag-review-title"
    >
      <header className={styles.inspectorHeader}>
        <span>
          <ShieldCheck aria-hidden="true" size={15} />
        </span>
        <div>
          <h2 id="tag-review-title">Tag 审核</h2>
          <small>修订 v{revised?.revision ?? "—"} · 提交版本</small>
        </div>
        <em>
          {props.bundle.schema.schema_id} v{props.bundle.schema.version}
        </em>
      </header>
      <div className={styles.inspectorScroll}>
        <section className={styles.panelSection}>
          <h3>
            标记问题位置
            <span>{props.selectedIssueIds.size} 处</span>
          </h3>
          <p className={styles.reviewIssueHint}>
            勾选存在问题的标注区间。未勾选任何问题时，提交后标注完成；勾选后进入待修改。
          </p>
          {intervalRows.length ? (
            <ul className={styles.reviewIssueList} aria-label="标注问题位置">
              {intervalRows.map((row) => {
                const selected = props.selectedIssueIds.has(
                  row.tag.annotation_id,
                );
                const active =
                  currentTag?.annotation_id === row.tag.annotation_id;
                const label = intervalTagLabel(row.tag, schemaIndex);
                return (
                  <li
                    data-active={active || undefined}
                    data-selected={selected || undefined}
                    key={row.tag.annotation_id}
                    style={{ "--tag-depth": row.depth } as CSSProperties}
                  >
                    <label>
                      <input
                        aria-label={`标记问题：${label}，${row.tag.start_step} 到 ${row.tag.end_step} 步`}
                        checked={selected}
                        type="checkbox"
                        onChange={() => {
                          props.clock.seek(
                            stepToTimelineNs(row.tag.start_step, frequencyHz),
                          );
                          props.onToggleIssue(row.tag.annotation_id);
                        }}
                      />
                      <span>
                        <strong>{label}</strong>
                        <small>
                          {row.displayPath.join(" / ")} · [{row.tag.start_step},{" "}
                          {row.tag.end_step})
                        </small>
                      </span>
                      <em>{active ? "当前" : selected ? "有问题" : "正常"}</em>
                    </label>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className={styles.emptyText}>
              提交版本中没有可标记的 Tag 区间。
            </p>
          )}
        </section>
        <section className={styles.panelSection}>
          <h3>六项审核清单</h3>
          <CheckList
            checks={checks.map((check) => ({
              ...check,
              evidence: serverChecks.get(check.kind) ?? check.evidence,
            }))}
          />
        </section>
        <section className={styles.panelSection}>
          <h3>
            当前时间戳 <code>第 {currentStep.toLocaleString("zh-CN")} 步</code>
          </h3>
          {currentTag ? (
            <>
              <PathCrumbs
                path={
                  currentRow?.displayPath ?? [
                    intervalTagLabel(currentTag, schemaIndex),
                  ]
                }
              />
              <dl className={styles.propertyTable}>
                <div>
                  <dt>区间</dt>
                  <dd>
                    <code>
                      [{currentTag.start_step}, {currentTag.end_step})
                    </code>
                  </dd>
                </div>
                {Object.entries(currentTag.attributes ?? {}).map(
                  ([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ),
                )}
                {currentTag.relations.map((relation) => (
                  <div
                    key={`${relation.relation_type}:${relation.target.object_id}`}
                  >
                    <dt>{relation.relation_type}</dt>
                    <dd>
                      {relation.target.object_type} /{" "}
                      {relation.target.object_id}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <p className={styles.emptyText}>当前时间戳没有 Tag 区间。</p>
          )}
        </section>
        <section className={styles.panelSection}>
          <h3>
            <GitCompareArrows aria-hidden="true" size={14} /> 原始 / 修订差异
          </h3>
          <div
            className={styles.diffGrid}
            role="table"
            aria-label="原始与修订 Tag 差异"
          >
            <div role="row">
              <strong role="columnheader">对比项</strong>
              <strong role="columnheader">
                原始 v{original?.revision ?? "—"}
              </strong>
              <strong role="columnheader">
                修订 v{revised?.revision ?? "—"}
              </strong>
            </div>
            <div role="row">
              <span role="cell">层级路径</span>
              <span role="cell">
                {originalRow?.displayPath.join(" / ") ?? "无对应项"}
              </span>
              <span role="cell">
                {currentRow?.displayPath.join(" / ") ?? "无"}
              </span>
            </div>
            <div role="row">
              <span role="cell">开始步</span>
              <code role="cell">{originalTag?.start_step ?? "—"}</code>
              <code role="cell">{currentTag?.start_step ?? "—"}</code>
            </div>
            <div role="row">
              <span role="cell">结束步</span>
              <code role="cell">{originalTag?.end_step ?? "—"}</code>
              <code role="cell">{currentTag?.end_step ?? "—"}</code>
            </div>
            <div role="row">
              <span role="cell">属性数</span>
              <code role="cell">
                {Object.keys(originalTag?.attributes ?? {}).length}
              </code>
              <code role="cell">
                {Object.keys(currentTag?.attributes ?? {}).length}
              </code>
            </div>
          </div>
        </section>
        <section className={styles.panelSection}>
          <label className={styles.commentField} htmlFor="tag-review-comment">
            <span>补充审核意见（可选）</span>
            <textarea
              autoComplete="off"
              id="tag-review-comment"
              maxLength={500}
              name="tag-review-comment"
              placeholder="补充说明问题原因或修改要求…"
              value={props.comment}
              onChange={(event) => props.onCommentChange(event.target.value)}
            />
            <output>{props.comment.length} / 500</output>
          </label>
        </section>
      </div>
    </section>
  );
}

function DataViewInspector(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly tags: readonly RuntimeAnnotationTag[];
}): JSX.Element {
  const latest = props.bundle.history.revisions.at(-1);
  return (
    <section
      className={styles.inspectorPanel}
      aria-labelledby="data-view-title"
    >
      <header className={styles.inspectorHeader}>
        <span>
          <GitCompareArrows aria-hidden="true" size={15} />
        </span>
        <div>
          <h2 id="data-view-title">数据查看</h2>
          <small>只读预览 · 固定 Episode / rollout 事实</small>
        </div>
        <em>r{latest?.revision ?? props.bundle.task.current_revision}</em>
      </header>
      <div className={styles.inspectorScroll}>
        <section className={styles.panelSection}>
          <h3>当前数据身份</h3>
          <dl className={styles.propertyTable}>
            <div>
              <dt>Rollout</dt>
              <dd>{props.bundle.task.rollout_id}</dd>
            </div>
            <div>
              <dt>Dataset</dt>
              <dd>
                {props.bundle.task.dataset_id} v
                {props.bundle.task.dataset_version}
              </dd>
            </div>
            <div>
              <dt>Lance 基线</dt>
              <dd>v{props.bundle.task.base_lance_version}</dd>
            </div>
            <div>
              <dt>步数</dt>
              <dd>{props.bundle.task.base_step_count ?? "未知"}</dd>
            </div>
            <div>
              <dt>标签结构</dt>
              <dd>
                {props.bundle.schema.schema_id} v{props.bundle.schema.version}
              </dd>
            </div>
          </dl>
        </section>
        <section className={styles.panelSection}>
          <h3>当前修订概要</h3>
          <p className={styles.emptyText}>
            {props.tags.length} 个 Tag 区间 · {latest?.operations.length ?? 0}{" "}
            个数据操作。 此状态只读，媒体、数值流和时间轴继续使用同一工作台。
          </p>
        </section>
      </div>
    </section>
  );
}

function RevisionHistoryInspector(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly selectedRevision: number | null;
  readonly disabled: boolean;
  readonly onSelectRevision: (revision: number) => void;
}): JSX.Element {
  const revisions = props.bundle.history.revisions.toSorted(
    (left, right) => right.revision - left.revision,
  );
  return (
    <section
      className={styles.inspectorPanel}
      aria-labelledby="revision-history-title"
    >
      <header className={styles.inspectorHeader}>
        <span>
          <RotateCcw aria-hidden="true" size={15} />
        </span>
        <div>
          <h2 id="revision-history-title">不可变数据修订</h2>
          <small>查看历史 · 选择旧修订并追加安全回退</small>
        </div>
        <em>{revisions.length} 个版本</em>
      </header>
      <div className={styles.inspectorScroll}>
        <ol
          className={styles.revisionHistoryList}
          aria-label="不可变数据修订历史"
        >
          {revisions.map((revision) => {
            const current =
              revision.revision === props.bundle.task.current_revision;
            return (
              <li data-current={current || undefined} key={revision.revision}>
                <button
                  aria-pressed={props.selectedRevision === revision.revision}
                  disabled={props.disabled || current}
                  type="button"
                  onClick={() => props.onSelectRevision(revision.revision)}
                >
                  <span>
                    <strong>r{revision.revision}</strong>
                    <small>{current ? "当前版本" : "历史版本"}</small>
                  </span>
                  <span>
                    {revision.origin} · {revision.author_id}
                    <small>
                      {revision.tags.length} Tags · {revision.operations.length}{" "}
                      数据操作
                    </small>
                  </span>
                  <time dateTime={revision.created_at}>
                    {revision.created_at
                      ? displayRevisionTime(revision.created_at)
                      : "时间未提供"}
                  </time>
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}

function PreSubmitCheckPanel(props: {
  readonly state: Exclude<
    PreSubmitCheckState,
    { readonly status: "idle" | "passed" }
  >;
}): JSX.Element {
  if (props.state.status === "checking") {
    return (
      <section
        className={styles.preSubmitCheck}
        aria-labelledby="pre-submit-check-title"
        role="status"
      >
        <h3 id="pre-submit-check-title">
          <LoaderCircle
            aria-hidden="true"
            className={styles.loadingIcon}
            size={15}
          />
          提交前检查
        </h3>
        <p>正在检查当前已保存草稿，请稍候…</p>
      </section>
    );
  }
  if (props.state.status === "error") {
    return (
      <section
        className={styles.preSubmitCheck}
        aria-labelledby="pre-submit-check-title"
        data-status="error"
        role="alert"
      >
        <h3 id="pre-submit-check-title">
          <CircleAlert aria-hidden="true" size={15} />
          提交前检查
        </h3>
        <p>提交前检查失败，请稍后重试</p>
      </section>
    );
  }
  return (
    <section
      className={styles.preSubmitCheck}
      aria-labelledby="pre-submit-check-title"
      data-status="failed"
      role="alert"
    >
      <h3 id="pre-submit-check-title">
        <CircleAlert aria-hidden="true" size={15} />
        提交前检查未通过
      </h3>
      <p>请根据阻断项修改标注并保存，然后重新提交审核。</p>
      <CheckList checks={props.state.checks} />
    </section>
  );
}

interface WorkspaceModeItem {
  readonly id: AnnotationWorkspaceMode;
  readonly label: string;
  readonly description: string;
  readonly icon: ReactNode;
  readonly available: boolean;
  readonly unavailableReason?: string;
}

function TooltipAction(props: {
  readonly className?: string;
  readonly busy?: boolean;
  readonly disabled: boolean;
  readonly label: string;
  readonly tooltip: string;
  readonly children: ReactNode;
  readonly onClick: () => void;
}): JSX.Element {
  return (
    <Tooltip title={props.tooltip} trigger={["hover", "focus"]}>
      <span
        aria-label={
          props.disabled ? `${props.label}：${props.tooltip}` : undefined
        }
        className={styles.actionTooltipTarget}
        tabIndex={props.disabled ? 0 : undefined}
      >
        <button
          aria-busy={props.busy || undefined}
          className={props.className}
          disabled={props.disabled}
          type="button"
          onClick={props.onClick}
        >
          {props.children}
        </button>
      </span>
    </Tooltip>
  );
}

function WorkspaceToolbar(props: {
  readonly requestedRouteMode: AnnotationWorkbenchMode;
  readonly mode: AnnotationWorkspaceMode;
  readonly taskStatus: RuntimeAnnotationBundle["task"]["status"];
  readonly navigationPermissions: AnnotationWorkbenchPermissions;
  readonly permissions: AnnotationWorkbenchPermissions;
  readonly pending: PendingAction;
  readonly dirty: boolean;
  readonly reviewBlocked: boolean;
  readonly reviewIssueCount: number;
  readonly restoreCandidates: readonly {
    readonly revision: number;
    readonly origin: string;
  }[];
  readonly restoreTargetRevision: number | null;
  readonly createButtonRef: RefObject<HTMLSpanElement | null>;
  readonly onRequestMode: (mode: AnnotationWorkspaceMode) => void;
  readonly onCreate: () => void;
  readonly onOpenSave: () => void;
  readonly onOpenSubmit: () => void;
  readonly onOpenReview: (decision: RuntimeReviewDecision) => void;
  readonly onRestoreTargetChange: (revision: number | null) => void;
  readonly onOpenRestore: (revision: number) => void;
}): JSX.Element {
  const [lockedMessage, setLockedMessage] = useState<string | null>(null);
  const modeRefs = useRef<
    Partial<Record<AnnotationWorkspaceMode, HTMLButtonElement | null>>
  >({});
  const items: readonly WorkspaceModeItem[] = [
    {
      id: "view",
      label: "查看",
      description: "浏览原始数据",
      icon: <Eye aria-hidden="true" size={18} />,
      available: true,
    },
    {
      id: "annotation",
      label: "标注",
      description: "创建或编辑标注",
      icon: <Tags aria-hidden="true" size={18} />,
      available:
        props.navigationPermissions.hasAnnotationDraft &&
        props.navigationPermissions.canEdit,
      unavailableReason:
        props.navigationPermissions.annotationUnavailableReason ??
        "请先创建标注草稿。",
    },
    {
      id: "tag-review",
      label: "Tag 审核",
      description: "检查并审批标注",
      icon: <ShieldCheck aria-hidden="true" size={18} />,
      available: props.navigationPermissions.canReview,
      unavailableReason:
        props.navigationPermissions.reviewUnavailableReason ??
        "当前没有可审核的提交。",
    },
    {
      id: "revisions",
      label: "数据修订",
      description: "修正数据问题",
      icon: <Wrench aria-hidden="true" size={18} />,
      available: props.navigationPermissions.canRevise,
      unavailableReason:
        props.navigationPermissions.revisionUnavailableReason ??
        "当前状态不允许写入数据修订。",
    },
  ];
  const status =
    props.mode === "annotation"
      ? { label: "编辑模式", icon: <PencilLine aria-hidden="true" size={14} /> }
      : props.mode === "tag-review"
        ? {
            label: "审核模式",
            icon: <ShieldCheck aria-hidden="true" size={14} />,
          }
        : props.mode === "revisions"
          ? { label: "修订模式", icon: <Wrench aria-hidden="true" size={14} /> }
          : props.taskStatus === "SUBMITTED"
            ? {
                label: "审核中",
                icon: <ShieldCheck aria-hidden="true" size={14} />,
              }
            : props.taskStatus === "APPROVED"
              ? {
                  label: "已通过",
                  icon: <Check aria-hidden="true" size={14} />,
                }
              : props.taskStatus === "NEEDS_REVISION"
                ? {
                    label: "要求修改",
                    icon: <PencilLine aria-hidden="true" size={14} />,
                  }
                : props.taskStatus === "REJECTED"
                  ? {
                      label: "已拒绝",
                      icon: <CircleAlert aria-hidden="true" size={14} />,
                    }
                  : {
                      label: "查看模式",
                      icon: <Eye aria-hidden="true" size={14} />,
                    };
  const hint =
    props.requestedRouteMode === "tag-review" &&
    !props.navigationPermissions.canReview
      ? {
          tone: "empty",
          text: `当前没有待审核提交。${props.navigationPermissions.reviewUnavailableReason ?? ""}`,
        }
      : props.mode === "annotation"
        ? {
            tone: "edit",
            text: "正在编辑标注草稿。请保存修改后再提交审核。",
          }
        : props.mode === "tag-review"
          ? {
              tone: "review",
              text:
                props.reviewIssueCount > 0
                  ? `已标记 ${props.reviewIssueCount} 处问题，提交审核后任务进入待修改。`
                  : "尚未标记问题，提交审核后任务将标注完成。",
            }
          : props.mode === "revisions"
            ? {
                tone: "revision",
                text: "正在查看不可变数据修订。回退会创建新修订，不会覆盖历史版本。",
              }
            : {
                tone: "view",
                text: "当前处于查看模式，所有编辑操作均已锁定。创建标注后才能修改 Tag 区间和数据修订。",
              };

  const requestMode = (item: WorkspaceModeItem) => {
    if (!item.available) {
      const reason = item.unavailableReason ?? "当前模式不可用。";
      setLockedMessage(reason);
      if (
        item.id === "annotation" &&
        !props.navigationPermissions.hasAnnotationDraft &&
        props.createButtonRef.current
      ) {
        const createButton =
          props.createButtonRef.current.querySelector<HTMLButtonElement>(
            "button",
          );
        if (createButton && !createButton.disabled) createButton.focus();
        else props.createButtonRef.current.focus();
      }
      return;
    }
    setLockedMessage(null);
    props.onRequestMode(item.id);
  };
  const onModeKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    for (let offset = 1; offset <= items.length; offset += 1) {
      const index =
        (currentIndex + direction * offset + items.length) % items.length;
      const item = items[index];
      if (!item?.available) continue;
      modeRefs.current[item.id]?.focus();
      requestMode(item);
      break;
    }
  };
  const busy = props.pending !== null;
  const pendingAnnouncement =
    props.pending === "create"
      ? "正在创建标注草稿"
      : props.pending === "save"
        ? "正在保存标注修改"
        : props.pending === "precheck"
          ? "正在执行提交前检查"
          : props.pending === "submit"
            ? "正在提交审核"
            : props.pending === "restore"
              ? "正在写入数据修订"
              : props.pending
                ? `正在处理${reviewDecisionLabels[props.pending]}`
                : "";

  return (
    <div className={styles.workspaceCard}>
      <div className={styles.workspaceBar}>
        <div
          aria-label="数据标注工作模式"
          className={styles.workspaceModes}
          role="tablist"
        >
          {items.map((item, index) => (
            <Tooltip
              key={item.id}
              title={item.available ? item.description : item.unavailableReason}
              trigger={["hover", "focus"]}
            >
              <button
                aria-disabled={!item.available || busy}
                aria-selected={props.mode === item.id}
                className={styles.workspaceMode}
                data-locked={!item.available || undefined}
                ref={(node) => {
                  modeRefs.current[item.id] = node;
                }}
                role="tab"
                tabIndex={props.mode === item.id || !item.available ? 0 : -1}
                type="button"
                onClick={() => {
                  if (!busy) requestMode(item);
                }}
                onKeyDown={(event) => onModeKeyDown(event, index)}
              >
                <span className={styles.workspaceModeIcon}>
                  {item.available ? (
                    item.icon
                  ) : (
                    <LockKeyhole aria-hidden="true" size={17} />
                  )}
                </span>
                <span className={styles.workspaceModeCopy}>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
                {props.mode === item.id ? (
                  <Check
                    aria-hidden="true"
                    className={styles.workspaceModeCheck}
                    size={14}
                  />
                ) : null}
              </button>
            </Tooltip>
          ))}
        </div>
        <div className={styles.workspaceContext}>
          <span
            className={styles.workspaceStatus}
            data-mode={props.mode}
            data-task-status={props.taskStatus}
          >
            {status.icon}
            {status.label}
          </span>
          <div className={styles.workspaceActions}>
            {props.mode === "tag-review" ? (
              <TooltipAction
                busy={
                  props.pending === "APPROVE" ||
                  props.pending === "NEEDS_REVISION"
                }
                className={
                  props.reviewIssueCount > 0
                    ? styles.requestChangesAction
                    : styles.approveAction
                }
                disabled={
                  !props.permissions.canReview ||
                  (props.reviewBlocked && props.reviewIssueCount === 0) ||
                  busy
                }
                label="提交审核"
                tooltip={
                  props.reviewBlocked && props.reviewIssueCount === 0
                    ? "自动检查未通过，请先标记对应的问题位置。"
                    : props.reviewIssueCount > 0
                      ? `提交后进入待修改（${props.reviewIssueCount} 处问题）。`
                      : "未标记问题，提交后标注完成。"
                }
                onClick={() =>
                  props.onOpenReview(
                    props.reviewIssueCount > 0 ? "NEEDS_REVISION" : "APPROVE",
                  )
                }
              >
                {props.pending === "APPROVE" ||
                props.pending === "NEEDS_REVISION" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className={styles.loadingIcon}
                    size={15}
                  />
                ) : (
                  <Send aria-hidden="true" size={15} />
                )}
                提交审核
              </TooltipAction>
            ) : props.mode === "annotation" ? (
              <>
                <TooltipAction
                  busy={props.pending === "save"}
                  disabled={!props.permissions.canSave || !props.dirty || busy}
                  label="保存修改"
                  tooltip={
                    !props.dirty
                      ? "暂无需要保存的修改"
                      : "保存当前修改为草稿，不会进入审核。"
                  }
                  onClick={props.onOpenSave}
                >
                  {props.pending === "save" ? (
                    <LoaderCircle
                      aria-hidden="true"
                      className={styles.loadingIcon}
                      size={15}
                    />
                  ) : (
                    <Save aria-hidden="true" size={15} />
                  )}
                  保存修改
                </TooltipAction>
                <TooltipAction
                  busy={
                    props.pending === "precheck" || props.pending === "submit"
                  }
                  className={styles.submitAction}
                  disabled={!props.permissions.canSubmit || props.dirty || busy}
                  label="提交审核"
                  tooltip={
                    props.dirty
                      ? "请先保存当前修改"
                      : "提交后将进入审核流程，可能无法继续编辑。"
                  }
                  onClick={props.onOpenSubmit}
                >
                  {props.pending === "precheck" ||
                  props.pending === "submit" ? (
                    <LoaderCircle
                      aria-hidden="true"
                      className={styles.loadingIcon}
                      size={15}
                    />
                  ) : (
                    <Send aria-hidden="true" size={15} />
                  )}
                  提交审核
                </TooltipAction>
              </>
            ) : props.mode === "revisions" ? (
              <>
                <label
                  className={styles.restoreSelect}
                  htmlFor="annotation-restore-revision"
                >
                  <span className={styles.srOnly}>回退目标修订</span>
                  <select
                    id="annotation-restore-revision"
                    name="annotation-restore-revision"
                    value={props.restoreTargetRevision ?? ""}
                    onChange={(event) => {
                      const selected = event.target.value;
                      props.onRestoreTargetChange(
                        selected === "" ? null : Number(selected),
                      );
                    }}
                  >
                    <option value="">选择历史修订</option>
                    {props.restoreCandidates.map((revision) => (
                      <option key={revision.revision} value={revision.revision}>
                        r{revision.revision} · {revision.origin}
                      </option>
                    ))}
                  </select>
                </label>
                <TooltipAction
                  busy={props.pending === "restore"}
                  disabled={
                    !props.permissions.canRevise ||
                    props.dirty ||
                    busy ||
                    props.restoreTargetRevision === null
                  }
                  label="回退为该修订"
                  tooltip={
                    props.dirty
                      ? "请先保存当前修改"
                      : "回退会创建新的不可变数据修订。"
                  }
                  onClick={() => {
                    if (props.restoreTargetRevision !== null)
                      props.onOpenRestore(props.restoreTargetRevision);
                  }}
                >
                  {props.pending === "restore" ? (
                    <LoaderCircle
                      aria-hidden="true"
                      className={styles.loadingIcon}
                      size={15}
                    />
                  ) : (
                    <RotateCcw aria-hidden="true" size={14} />
                  )}
                  回退为该修订
                </TooltipAction>
              </>
            ) : !props.permissions.hasAnnotationDraft &&
              props.requestedRouteMode === "annotation" &&
              props.taskStatus === "DRAFT" ? (
              <Tooltip
                title={
                  props.permissions.canCreate
                    ? "创建标注草稿后可编辑 Tag 区间和数据修订。"
                    : props.permissions.createUnavailableReason
                }
                trigger={["hover", "focus"]}
              >
                <span
                  className={styles.actionTooltipTarget}
                  ref={props.createButtonRef}
                  tabIndex={props.permissions.canCreate ? -1 : 0}
                >
                  <button
                    aria-disabled={!props.permissions.canCreate || busy}
                    aria-busy={props.pending === "create" || undefined}
                    className={styles.createAction}
                    disabled={!props.permissions.canCreate || busy}
                    type="button"
                    onClick={() => {
                      if (props.permissions.canCreate) props.onCreate();
                      else
                        setLockedMessage(
                          props.permissions.createUnavailableReason ??
                            "当前账号不能创建标注草稿。",
                        );
                    }}
                  >
                    {props.pending === "create" ? (
                      <LoaderCircle
                        aria-hidden="true"
                        className={styles.loadingIcon}
                        size={15}
                      />
                    ) : (
                      <FilePlus2 aria-hidden="true" size={15} />
                    )}
                    创建标注
                  </button>
                </span>
              </Tooltip>
            ) : null}
          </div>
        </div>
      </div>
      <div
        aria-live="polite"
        className={styles.workspaceHint}
        data-tone={lockedMessage ? "locked" : hint.tone}
        role={lockedMessage || hint.tone === "empty" ? "status" : undefined}
      >
        {lockedMessage ? (
          <LockKeyhole aria-hidden="true" size={15} />
        ) : hint.tone === "empty" ? (
          <CircleAlert aria-hidden="true" size={15} />
        ) : (
          <Info aria-hidden="true" size={15} />
        )}
        <span>{lockedMessage ?? hint.text}</span>
      </div>
      <span aria-live="polite" className={styles.srOnly}>
        {pendingAnnouncement}
      </span>
    </div>
  );
}

function ActionDock(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly surface: AnnotationWorkspaceMode;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly dirty: boolean;
  readonly permissions: AnnotationWorkbenchPermissions;
  readonly preSubmitCheck: PreSubmitCheckState;
  readonly onOpenRevisionLedger?: () => void;
}): JSX.Element {
  const tag = props.tags[0] ?? null;
  const schemaIndex = buildTagSchemaIndex(props.bundle.schema);
  const hierarchyRows = intervalHierarchyRows(props.tags, schemaIndex);
  const displayPath = tag
    ? (hierarchyRows.find((row) => row.tag.annotation_id === tag.annotation_id)
        ?.displayPath ?? [intervalTagLabel(tag, schemaIndex)])
    : [];
  const summary = (
    <section
      className={styles.dockSummary}
      aria-label={
        props.surface === "tag-review" ? "提交版本概要" : "当前 Tag 概要"
      }
    >
      <h3>
        {props.surface === "tag-review" ? "提交版本概要" : "当前 Tag 概要"}
      </h3>
      {tag ? (
        <>
          <PathCrumbs path={displayPath} />
          <dl>
            <div>
              <dt>数据结构</dt>
              <dd>
                {props.bundle.schema.schema_id} v{props.bundle.schema.version}
              </dd>
            </div>
            <div>
              <dt>区间</dt>
              <dd>
                <code>
                  [{tag.start_step}, {tag.end_step})
                </code>
              </dd>
            </div>
            {Object.entries(tag.attributes ?? {}).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
            {tag.relations.map((relation) => (
              <div
                key={`${relation.relation_type}:${relation.target.object_id}`}
              >
                <dt>{relation.relation_type}</dt>
                <dd>
                  {relation.target.object_type} /{" "}
                  {relation.target.object_id || "未填写"}
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p>当前没有 Tag 区间。</p>
      )}
    </section>
  );
  if (props.surface === "tag-review") {
    return (
      <section className={styles.actionDock} aria-label="Tag 审核提交概要">
        {summary}
        <p>
          <LockKeyhole aria-hidden="true" size={13} />
          审核主操作位于页面顶部工作台栏，只对固定提交版本创建不可变记录。
        </p>
        {props.permissions.readOnlyReason ? (
          <small>{props.permissions.readOnlyReason}</small>
        ) : null}
      </section>
    );
  }
  if (props.surface === "view") {
    return (
      <section className={styles.actionDock} aria-label="数据查看状态">
        {summary}
        <p>
          <LockKeyhole aria-hidden="true" size={13} />
          查看状态不会修改 Tag、数据操作或不可变修订。
        </p>
        {props.permissions.readOnlyReason ? (
          <small>{props.permissions.readOnlyReason}</small>
        ) : null}
      </section>
    );
  }
  if (props.surface === "revisions") {
    return (
      <section className={styles.actionDock} aria-label="数据修订概要">
        {summary}
        <p>
          <LockKeyhole aria-hidden="true" size={13} />
          修订选择与回退操作已集中到页面顶部工作台栏。
        </p>
        {props.onOpenRevisionLedger ? (
          <button type="button" onClick={props.onOpenRevisionLedger}>
            打开全局修订列表
          </button>
        ) : null}
      </section>
    );
  }
  return (
    <section className={styles.actionDock} aria-label="标注草稿动作">
      {summary}
      {props.preSubmitCheck.status !== "idle" &&
      props.preSubmitCheck.status !== "passed" ? (
        <PreSubmitCheckPanel state={props.preSubmitCheck} />
      ) : null}
      <p data-dirty={props.dirty || undefined}>
        {props.dirty ? "有未保存的更改" : "草稿已与服务器同步"}
      </p>
      {props.permissions.readOnlyReason ? (
        <small>{props.permissions.readOnlyReason}</small>
      ) : null}
    </section>
  );
}

function errorMessage(
  error: unknown,
): { message: string; requestId?: string; conflict: boolean } | null {
  if (!error) return null;
  if (isDomainError(error)) {
    return {
      message: `${error.message}${error.problemCode ? `（问题代码：${error.problemCode}）` : ""}${error.retryable ? "；当前数据已保留，可重试。" : "；当前数据已保留。"}`,
      ...(error.requestId ? { requestId: error.requestId } : {}),
      conflict:
        error.code === "VERSION_CONFLICT" ||
        error.code === "PRECONDITION_FAILED",
    };
  }
  return {
    message: `${error instanceof Error ? error.message : "操作未完成。"}；当前数据已保留，可再次尝试。`,
    conflict: false,
  };
}

export function AnnotationWorkbenchView(
  props: AnnotationWorkbenchViewProps,
): JSX.Element {
  const [selection, setSelection] = useState<StepSelection | null>(null);
  const [cameraView, setCameraView] = useState(
    () =>
      (props.mode === "tag-review"
        ? props.bundle.manifest?.cameras[0]?.camera_id
        : undefined) ?? "__quad__",
  );
  const [reviewComment, setReviewComment] = useState("");
  const [reviewIssueIds, setReviewIssueIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [pending, setPending] = useState<PendingAction>(null);
  const [preSubmitCheckState, setPreSubmitCheckState] =
    useState<PreSubmitCheckState>(idlePreSubmitCheck);
  const [localError, setLocalError] = useState<unknown>(null);
  const [dialogDecision, setDialogDecision] = useState<DialogDecision>(null);
  const [dataInfoOpen, setDataInfoOpen] = useState(false);
  const [reportDialogOpen, setReportDialogOpen] = useState(false);
  const [reportStreamRef, setReportStreamRef] = useState("");
  const [reportIssueType, setReportIssueType] =
    useState<ManualIssueType>("OTHER");
  const [reportSeverity, setReportSeverity] =
    useState<ManualIssueSeverity>("MEDIUM");
  const [reportNote, setReportNote] = useState("");
  const [reportPending, setReportPending] = useState(false);
  const [reportError, setReportError] = useState<unknown>(null);
  const [reportSuccess, setReportSuccess] = useState<string | null>(null);
  const dataInfoTriggerRef = useRef<HTMLButtonElement>(null);
  const createButtonRef = useRef<HTMLSpanElement>(null);
  const [workspaceMode, setWorkspaceMode] = useState<AnnotationWorkspaceMode>(
    () =>
      props.mode === "tag-review" && props.permissions.canReview
        ? "tag-review"
        : props.permissions.hasAnnotationDraft && props.permissions.canEdit
          ? "annotation"
          : "view",
  );
  const [modeSwitchTarget, setModeSwitchTarget] =
    useState<AnnotationWorkspaceMode | null>(null);
  const [restoreTargetRevision, setRestoreTargetRevision] = useState<
    number | null
  >(null);
  const [resourceErrors, setResourceErrors] = useState<readonly string[]>([]);
  const actionInFlightRef = useRef(false);
  const currentTaskIdRef = useRef(props.bundle.task.task_id);
  const currentTagsRef = useRef(props.tags);
  currentTaskIdRef.current = props.bundle.task.task_id;
  currentTagsRef.current = props.tags;
  const preSubmitCheck =
    preSubmitCheckState.status !== "idle" &&
    preSubmitCheckState.taskId === props.bundle.task.task_id &&
    preSubmitCheckState.tags === props.tags
      ? preSubmitCheckState
      : idlePreSubmitCheck;
  const visibleError = errorMessage(localError ?? props.externalError);
  const permissions = visibleError?.conflict
    ? {
        ...props.permissions,
        canCreate: false,
        canEdit: false,
        canSave: false,
        canSubmit: false,
        canReview: false,
        canRevise: false,
        createUnavailableReason: "并发版本已变化，刷新真实任务后才能创建草稿。",
        readOnlyReason: "并发版本已变化；刷新真实任务后才能继续写入。",
      }
    : props.permissions;
  useEffect(() => {
    if (workspaceMode === "annotation" && !props.permissions.canEdit)
      setWorkspaceMode("view");
    else if (workspaceMode === "tag-review" && !props.permissions.canReview)
      setWorkspaceMode("view");
    else if (workspaceMode === "revisions" && !props.permissions.canRevise)
      setWorkspaceMode("view");
  }, [
    props.permissions.canEdit,
    props.permissions.canReview,
    props.permissions.canRevise,
    workspaceMode,
  ]);
  const stepCount = Math.max(1, props.bundle.task.base_step_count ?? 1);
  const frequencyHz = bundleFrequencyHz(props.bundle);
  const clock = useMemo(
    () =>
      createPlaybackClock({
        startNs: "0",
        endNs: stepToTimelineNs(stepCount, frequencyHz),
      }),
    [frequencyHz, props.bundle.task.task_id, stepCount],
  );
  const clockDisposalTokens = useRef(
    new Map<ReturnType<typeof createPlaybackClock>, symbol>(),
  );
  useEffect(() => {
    const token = Symbol("annotation-clock-lifecycle");
    const tokens = clockDisposalTokens.current;
    tokens.set(clock, token);
    return () => {
      queueMicrotask(() => {
        if (tokens.get(clock) !== token) return;
        clock.dispose();
        tokens.delete(clock);
      });
    };
  }, [clock]);
  const reviewChecks =
    workspaceMode === "tag-review"
      ? evaluateAnnotationTags({
          task: props.bundle.task,
          schema: props.bundle.schema,
          tags: props.tags,
        })
      : [];
  const reviewIssueRows = useMemo(() => {
    const index = buildTagSchemaIndex(props.bundle.schema);
    return intervalHierarchyRows(props.tags, index).filter((row) =>
      reviewIssueIds.has(row.tag.annotation_id),
    );
  }, [props.bundle.schema, props.tags, reviewIssueIds]);
  const restoreCandidates = props.bundle.history.revisions
    .filter(
      (revision) => revision.revision < props.bundle.task.current_revision,
    )
    .toSorted((left, right) => right.revision - left.revision);
  const timelineSelection = useMemo<ViewerTimelineSelection | undefined>(
    () =>
      selection
        ? {
            startNs: stepToTimelineNs(selection.startStep, frequencyHz),
            endNs: stepToTimelineNs(selection.endStep, frequencyHz),
            label: `${selection.startStep}–${selection.endStep} 步`,
          }
        : undefined,
    [frequencyHz, selection],
  );
  const onTimeRangeSelect = useCallback(
    (startNs: string, endNs: string) => {
      if (!permissions.canEdit || workspaceMode !== "annotation") return;
      const startStep = timelineNsToStep(startNs, frequencyHz);
      setSelection({
        startStep,
        endStep: Math.max(startStep + 1, timelineNsToStep(endNs, frequencyHz)),
      });
    },
    [frequencyHz, permissions.canEdit, workspaceMode],
  );
  const onTagsChange = useCallback(
    (tags: readonly RuntimeAnnotationTag[]) => {
      if (
        !permissions.canEdit ||
        workspaceMode !== "annotation" ||
        actionInFlightRef.current
      )
        return;
      props.onTagsChange(tags);
    },
    [permissions.canEdit, props.onTagsChange, workspaceMode],
  );
  const onResourceError = useCallback((error: DomainError) => {
    const message = `${error.message}${error.requestId ? `（${error.requestId}）` : ""}`;
    setResourceErrors((current) =>
      current.includes(message) ? current : [...current, message],
    );
  }, []);
  const cameraStreams = useMemo(() => {
    const adapterMode: AnnotationWorkbenchMode =
      workspaceMode === "tag-review" ? "tag-review" : "annotation";
    return buildRuntimeCameraStreams({
      bundle: props.bundle,
      scope: props.scope,
      mode: adapterMode,
      ...(cameraView !== "__quad__" && cameraView !== "__all__"
        ? { selectedCameraId: cameraView }
        : {}),
      ...(cameraView === "__quad__" ? { cameraLimit: 4 } : {}),
      ...(cameraView === "__quad__" ? { cameraSlotCount: 4 } : {}),
    });
  }, [cameraView, props.bundle, props.scope, workspaceMode]);
  const adapter = useMemo(() => {
    const adapterMode: AnnotationWorkbenchMode =
      workspaceMode === "tag-review" ? "tag-review" : "annotation";
    return {
      ...buildRuntimeWorkbenchAdapter({
        bundle: props.bundle,
        scope: props.scope,
        mode: adapterMode,
        clock,
        tags: props.tags,
        ...(cameraView !== "__quad__" && cameraView !== "__all__"
          ? { selectedCameraId: cameraView }
          : {}),
        ...(cameraView === "__quad__" ? { cameraLimit: 4 } : {}),
        ...(cameraView === "__quad__" ? { cameraSlotCount: 4 } : {}),
        readOnly: workspaceMode !== "annotation" || !permissions.canEdit,
        ...(props.onSelectTask && pending === null
          ? { onSelectTask: props.onSelectTask }
          : {}),
        ...(timelineSelection ? { timelineSelection } : {}),
        ...(!permissions.canEdit || workspaceMode !== "annotation"
          ? {}
          : { onTimeRangeSelect }),
        onResourceError,
      }),
      cameraStreams,
    };
  }, [
    cameraStreams,
    clock,
    onResourceError,
    onTimeRangeSelect,
    permissions.canEdit,
    pending,
    props.bundle,
    props.onSelectTask,
    props.scope,
    props.tags,
    cameraView,
    workspaceMode,
    timelineSelection,
  ]);
  const jointTopic = props.bundle.manifest?.topics.find((topic) =>
    /(^|[/_.-])joint([/_\s.-]|$)/iu.test(topic.name),
  );
  const jointAngleStream = useMemo<StreamDescriptor | null>(
    () =>
      props.jointAngleStream ??
      buildRuntimeJointAngleStream({
        bundle: props.bundle,
        scope: props.scope,
      }),
    [props.bundle, props.jointAngleStream, props.scope],
  );
  const visualAdapter = useMemo(
    () => ({
      ...adapter,
      ...(props.robotScene
        ? { robotScene: { ...props.robotScene, clock } }
        : {
            robotSceneUnavailableReason: jointTopic
              ? (props.robotSceneUnavailableReason ??
                `已发现关节角数据 ${jointTopic.name}，但当前采集机器人尚未解析到已发布 URDF 绑定。请管理员完成机器人模型绑定。`)
              : "当前数据未发现关节角 Topic。绑定已发布 URDF 并写入关节角数据后，此处会随时间轴同步显示机器人姿态。",
          }),
    }),
    [
      adapter,
      clock,
      jointTopic,
      props.robotScene,
      props.robotSceneUnavailableReason,
    ],
  );
  const workbenchAdapter = useMemo(
    () =>
      workspaceMode === "annotation"
        ? {
            ...visualAdapter,
            robotScene: undefined,
            robotSceneUnavailableReason: undefined,
          }
        : visualAdapter,
    [visualAdapter, workspaceMode],
  );
  const invoke = async (
    action: PendingAction,
    task: () => Promise<void>,
  ): Promise<boolean> => {
    if (!action || pending || actionInFlightRef.current) return false;
    actionInFlightRef.current = true;
    setPending(action);
    setLocalError(null);
    try {
      await task();
      return true;
    } catch (error) {
      setLocalError(error);
      return false;
    } finally {
      setPending(null);
      actionInFlightRef.current = false;
    }
  };
  const runPreSubmitCheck = async () => {
    if (
      actionInFlightRef.current ||
      pending !== null ||
      dialogDecision !== null ||
      !permissions.canSubmit ||
      props.dirty
    )
      return;
    const taskId = props.bundle.task.task_id;
    const tags = props.tags;
    actionInFlightRef.current = true;
    setPending("precheck");
    setLocalError(null);
    setPreSubmitCheckState({ status: "checking", taskId, tags });
    try {
      const checks = props.onPreSubmitCheck
        ? await props.onPreSubmitCheck()
        : await Promise.resolve().then(() =>
            evaluateAnnotationTags({
              task: props.bundle.task,
              schema: props.bundle.schema,
              tags,
            }),
          );
      if (
        currentTaskIdRef.current !== taskId ||
        currentTagsRef.current !== tags
      )
        return;
      if (checks.some((check) => check.status === "FAIL")) {
        setPreSubmitCheckState({ status: "failed", taskId, tags, checks });
      } else {
        setPreSubmitCheckState({ status: "passed", taskId, tags, checks });
        setDialogDecision("SUBMIT");
      }
    } catch {
      if (
        currentTaskIdRef.current === taskId &&
        currentTagsRef.current === tags
      )
        setPreSubmitCheckState({ status: "error", taskId, tags });
    } finally {
      setPending(null);
      actionInFlightRef.current = false;
    }
  };
  const confirmDecision = () => {
    const decision = dialogDecision;
    setDialogDecision(null);
    if (decision === "SAVE" && permissions.canSave && props.dirty)
      void invoke("save", props.onSave);
    else if (decision === "SUBMIT") {
      if (permissions.canSubmit && preSubmitCheck.status === "passed") {
        setPreSubmitCheckState(idlePreSubmitCheck);
        void invoke("submit", props.onSubmit);
      }
    } else if (decision === "RESTORE") {
      if (
        permissions.canRevise &&
        restoreTargetRevision !== null &&
        props.onRestoreRevision
      )
        void invoke(
          "restore",
          () =>
            props.onRestoreRevision?.(restoreTargetRevision) ??
            Promise.resolve(),
        );
    } else if (
      (decision === "APPROVE" ||
        decision === "NEEDS_REVISION" ||
        decision === "REJECT") &&
      permissions.canReview
    )
      void invoke(decision, () =>
        props.onReview(
          decision,
          buildReviewComment(reviewIssueRows, reviewComment),
        ),
      );
  };
  const activateWorkspaceMode = (nextMode: AnnotationWorkspaceMode) => {
    setWorkspaceMode(nextMode);
    if (nextMode === "tag-review" && props.mode !== "tag-review")
      props.onSwitchMode?.("tag-review");
    else if (nextMode === "annotation" && props.mode !== "annotation")
      props.onSwitchMode?.("annotation");
  };
  const requestWorkspaceMode = (nextMode: AnnotationWorkspaceMode) => {
    if (nextMode === workspaceMode) return;
    if (workspaceMode === "annotation" && props.dirty) {
      setModeSwitchTarget(nextMode);
      return;
    }
    activateWorkspaceMode(nextMode);
  };
  const createAnnotation = () => {
    if (!permissions.canCreate || !props.onCreateAnnotation) return;
    void (async () => {
      const created = await invoke("create", props.onCreateAnnotation!);
      if (created) activateWorkspaceMode("annotation");
    })();
  };
  const discardAndSwitch = () => {
    const target = modeSwitchTarget;
    if (!target) return;
    props.onDiscardChanges?.();
    setPreSubmitCheckState(idlePreSubmitCheck);
    setModeSwitchTarget(null);
    activateWorkspaceMode(target);
  };
  const saveAndSwitch = () => {
    const target = modeSwitchTarget;
    if (!target || !permissions.canSave) return;
    void (async () => {
      const saved = await invoke("save", props.onSave);
      if (!saved) return;
      setModeSwitchTarget(null);
      activateWorkspaceMode(target);
    })();
  };
  const cameras = props.bundle.manifest?.cameras ?? [];
  const reportSelection = (() => {
    if (selection) return selection;
    const selectedTags = props.tags.filter((tag) =>
      reviewIssueIds.has(tag.annotation_id),
    );
    if (!selectedTags.length) return null;
    return {
      startStep: Math.min(...selectedTags.map((tag) => tag.start_step)),
      endStep: Math.max(...selectedTags.map((tag) => tag.end_step)),
    };
  })();
  const reportStreamOptions = [
    ...cameras.map((camera) => ({
      value: camera.topic,
      label: `${camera.camera_id} · ${camera.topic}`,
    })),
    ...(props.bundle.manifest?.topics ?? []).map((topic) => ({
      value: topic.name,
      label: topic.name,
    })),
  ].filter(
    (item, index, items) =>
      items.findIndex((candidate) => candidate.value === item.value) === index,
  );
  const reportProblem = errorMessage(reportError);
  const openReportDialog = () => {
    setReportError(null);
    setReportSuccess(null);
    setReportStreamRef(
      (current) => current || reportStreamOptions[0]?.value || "",
    );
    setReportDialogOpen(true);
  };
  const submitDataIssue = async () => {
    if (
      !props.onReportDataIssue ||
      !props.canReportDataIssue ||
      !reportSelection ||
      !reportStreamRef ||
      !reportNote.trim()
    )
      return;
    setReportPending(true);
    setReportError(null);
    try {
      await props.onReportDataIssue({
        streamRef: reportStreamRef,
        relativeStartNs: stepToTimelineNs(
          reportSelection.startStep,
          frequencyHz,
        ),
        relativeEndNs: stepToTimelineNs(reportSelection.endStep, frequencyHz),
        issueType: reportIssueType,
        severity: reportSeverity,
        note: reportNote.trim(),
      });
      setReportDialogOpen(false);
      setReportNote("");
      setReportSuccess("已登记为问题数据，可到“问题数据”页面继续分诊和处理。");
    } catch (error) {
      setReportError(error);
    } finally {
      setReportPending(false);
    }
  };
  const latestNeedsRevisionReview = props.bundle.history.reviews.findLast(
    (review) => review.decision === "NEEDS_REVISION",
  );
  const connectedCameraCount = workbenchAdapter.cameraStreams.filter(
    (stream) => stream.semanticRole !== "camera-slot-placeholder",
  ).length;
  const mediaHeader = (): ReactNode => (
    <div className={styles.cameraToolbar}>
      <div className={styles.cameraToolbarSummary}>
        <span>
          {workspaceMode === "annotation" && cameraView === "__quad__"
            ? `四宫格 · 已接入 ${connectedCameraCount} / 4 路`
            : `视频视图 · 当前 ${connectedCameraCount} / ${cameras.length} 路`}
        </span>
        <small>保持原始画面比例；视频、机器人姿态和信号共用时间轴。</small>
      </div>
      <div className={styles.cameraToolbarTools}>
        {cameras.length ? (
          <label
            htmlFor={
              workspaceMode === "tag-review"
                ? "review-camera-select"
                : "annotation-camera-select"
            }
          >
            <span>
              {workspaceMode === "tag-review" ? "当前相机" : "显示视频"}
            </span>
            <select
              aria-label={
                workspaceMode === "tag-review" ? "当前相机" : "显示视频"
              }
              id={
                workspaceMode === "tag-review"
                  ? "review-camera-select"
                  : "annotation-camera-select"
              }
              name="camera-view-select"
              value={
                workspaceMode === "tag-review" &&
                (cameraView === "__quad__" || cameraView === "__all__")
                  ? (cameras[0]?.camera_id ?? "")
                  : cameraView
              }
              onChange={(event) => setCameraView(event.target.value)}
            >
              {workspaceMode !== "tag-review" ? (
                <>
                  <option value="__quad__">四宫格（固定 4 格）</option>
                  <option value="__all__">全部视频（{cameras.length}）</option>
                </>
              ) : null}
              {cameras.map((camera) => (
                <option key={camera.camera_id} value={camera.camera_id}>
                  {camera.camera_id}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {props.onReportDataIssue ? (
          <Tooltip
            title={
              !props.canReportDataIssue
                ? (props.reportDataIssueUnavailableReason ??
                  "当前账号没有报告数据问题的权限。")
                : !reportSelection
                  ? "先在时间轴拖选异常片段；审核时也可以先勾选有问题的 Tag 区间。"
                  : "将当前异常片段登记到统一问题数据中心。"
            }
            trigger={["hover", "focus"]}
          >
            <span className={styles.reportIssueTooltipTarget}>
              <button
                className={styles.reportIssueTrigger}
                disabled={!props.canReportDataIssue || !reportSelection}
                type="button"
                onClick={openReportDialog}
              >
                <CircleAlert aria-hidden="true" size={13} />
                报告数据问题
              </button>
            </span>
          </Tooltip>
        ) : null}
        <button
          aria-expanded={dataInfoOpen}
          aria-haspopup="dialog"
          aria-label="数据信息"
          className={styles.dataInfoTrigger}
          ref={dataInfoTriggerRef}
          type="button"
          onClick={() => setDataInfoOpen((open) => !open)}
        >
          <Database aria-hidden="true" size={13} />
          数据信息
          <em aria-hidden="true">{adapter.collectionItems.length}</em>
        </button>
      </div>
    </div>
  );

  return (
    <div className={styles.page} data-p08-mode={props.mode}>
      {props.recoveryAvailable ? (
        <section className={styles.recoveryBanner} role="status">
          <RotateCcw aria-hidden="true" size={15} />
          <span>
            <strong>发现本会话未保存的 Tag 草稿</strong>
            恢复前不会覆盖服务器草稿。
          </span>
          <button type="button" onClick={props.onRecover}>
            恢复
          </button>
          <button type="button" onClick={props.onDiscardRecovery}>
            忽略
          </button>
        </section>
      ) : null}
      {visibleError ? (
        <section
          className={styles.operationError}
          data-conflict={visibleError.conflict || undefined}
          role="alert"
        >
          <AlertTriangle aria-hidden="true" size={16} />
          <span>
            <strong>
              {visibleError.conflict
                ? "并发版本冲突，写操作已暂停"
                : "操作未完成"}
            </strong>
            {visibleError.message}
            {visibleError.requestId
              ? `（请求 ID：${visibleError.requestId}）`
              : ""}
          </span>
        </section>
      ) : null}
      {reportSuccess ? (
        <section className={styles.reportSuccessNotice} role="status">
          <Check aria-hidden="true" size={15} />
          <span>{reportSuccess}</span>
        </section>
      ) : null}
      {props.bundle.task.status === "NEEDS_REVISION" &&
      latestNeedsRevisionReview ? (
        <section className={styles.reviewFeedbackNotice} role="status">
          <CircleAlert aria-hidden="true" size={16} />
          <span>
            <strong>审核待修改</strong>
            <small>
              审核人 {latestNeedsRevisionReview.reviewer_id} ·
              {displayRevisionTime(latestNeedsRevisionReview.created_at)}
            </small>
            <span>
              {latestNeedsRevisionReview.comment ||
                "请修改标注后重新提交审核。"}
            </span>
          </span>
        </section>
      ) : null}
      {resourceErrors.length ? (
        <section className={styles.resourceNotice} role="status">
          <AlertTriangle aria-hidden="true" size={14} />
          <span>部分预览加载失败：{resourceErrors.join("；")}</span>
        </section>
      ) : null}
      <div className={styles.workbenchFrame}>
        <DataVisualizationWorkbench
          adapter={workbenchAdapter}
          showNavigation={false}
          slots={{
            mediaHeader,
            ...(workspaceMode === "annotation"
              ? {
                  timelineTools: () => (
                    <div className={styles.timelineTagTools}>
                      <TagEditorInspector
                        bundle={props.bundle}
                        disabled={!permissions.canEdit || pending !== null}
                        selection={selection}
                        tags={props.tags}
                        onChange={onTagsChange}
                      />
                    </div>
                  ),
                }
              : {}),
            workspaceToolbar: () => (
              <WorkspaceToolbar
                createButtonRef={createButtonRef}
                dirty={props.dirty}
                mode={workspaceMode}
                navigationPermissions={props.permissions}
                pending={pending}
                permissions={permissions}
                requestedRouteMode={props.mode}
                restoreCandidates={restoreCandidates}
                restoreTargetRevision={restoreTargetRevision}
                reviewBlocked={reviewChecks.some(
                  (check) => check.status === "FAIL",
                )}
                reviewIssueCount={reviewIssueIds.size}
                taskStatus={props.bundle.task.status}
                onCreate={createAnnotation}
                onOpenRestore={(revision) => {
                  setRestoreTargetRevision(revision);
                  setDialogDecision("RESTORE");
                }}
                onOpenReview={setDialogDecision}
                onOpenSave={() => {
                  setPreSubmitCheckState(idlePreSubmitCheck);
                  setDialogDecision("SAVE");
                }}
                onOpenSubmit={() => void runPreSubmitCheck()}
                onRequestMode={requestWorkspaceMode}
                onRestoreTargetChange={setRestoreTargetRevision}
              />
            ),
            inspector: () =>
              workspaceMode === "tag-review" ? (
                <ReviewInspector
                  bundle={props.bundle}
                  clock={clock}
                  comment={reviewComment}
                  selectedIssueIds={reviewIssueIds}
                  tags={props.tags}
                  onCommentChange={setReviewComment}
                  onToggleIssue={(annotationId) =>
                    setReviewIssueIds((current) => {
                      const next = new Set(current);
                      if (next.has(annotationId)) next.delete(annotationId);
                      else next.add(annotationId);
                      return next;
                    })
                  }
                />
              ) : workspaceMode === "view" ? (
                <DataViewInspector bundle={props.bundle} tags={props.tags} />
              ) : workspaceMode === "revisions" ? (
                <RevisionHistoryInspector
                  bundle={props.bundle}
                  disabled={pending !== null || !permissions.canRevise}
                  selectedRevision={restoreTargetRevision}
                  onSelectRevision={setRestoreTargetRevision}
                />
              ) : (
                <div
                  className={styles.robotPoseInspector}
                  aria-label="机器人姿态同步视图"
                >
                  <ViewerRobotPosePanel
                    scene={visualAdapter.robotScene}
                    unavailableReason={
                      visualAdapter.robotSceneUnavailableReason
                    }
                  />
                </div>
              ),
            actionDock: () =>
              workspaceMode === "annotation" ? (
                <div className={styles.jointAngleDock}>
                  <ViewerJointAngleCurvePanel
                    clock={clock}
                    stream={jointAngleStream}
                    unavailableReason={
                      jointTopic
                        ? `已发现 ${jointTopic.name}，但当前固定数据窗口还没有可显示的关节角向量。`
                        : "当前数据未发现关节角 Topic。写入关节角数据后，曲线会随共享时间轴同步显示。"
                    }
                    onResourceError={onResourceError}
                  />
                  {preSubmitCheck.status !== "idle" &&
                  preSubmitCheck.status !== "passed" ? (
                    <PreSubmitCheckPanel state={preSubmitCheck} />
                  ) : null}
                </div>
              ) : (
                <ActionDock
                  bundle={props.bundle}
                  dirty={props.dirty}
                  surface={workspaceMode}
                  permissions={permissions}
                  preSubmitCheck={preSubmitCheck}
                  tags={props.tags}
                  onOpenRevisionLedger={props.onOpenRevisions}
                />
              ),
            ...(props.renderPanel ? { renderPanel: props.renderPanel } : {}),
          }}
        />
      </div>
      <EntityDrawer
        open={dataInfoOpen}
        returnFocusRef={dataInfoTriggerRef}
        title={
          <span className={styles.dataInfoDrawerTitle}>
            <Database aria-hidden="true" size={16} />
            <span>
              <strong>数据信息</strong>
              <small>采集条目与当前数据身份</small>
            </span>
          </span>
        }
        width={400}
        onClose={() => setDataInfoOpen(false)}
      >
        <div
          className={styles.dataInfoDrawer}
          id={`${adapter.id}-data-information`}
        >
          <WorkbenchCollectionPanel adapter={adapter} />
        </div>
      </EntityDrawer>
      <Modal
        centered
        destroyOnHidden
        footer={null}
        open={reportDialogOpen}
        title="报告数据问题"
        onCancel={() => {
          if (!reportPending) setReportDialogOpen(false);
        }}
      >
        <Form layout="vertical" onFinish={() => void submitDataIssue()}>
          <Alert
            className={styles.reportRangeAlert}
            showIcon
            type="info"
            title={
              reportSelection
                ? `异常片段：${reportSelection.startStep}–${reportSelection.endStep} 步（${stepRange(reportSelection.startStep, reportSelection.endStep, frequencyHz)}）`
                : "尚未选择异常片段"
            }
            description="这会创建独立的数据质量问题；不会自动改变当前标注任务的审核状态。"
          />
          <Form.Item
            label="问题数据流"
            required
            help="后端会从当前标注任务解析固定 Dataset、Episode、Revision 与 Stream 身份。"
          >
            <Select
              aria-label="问题数据流"
              disabled={reportPending}
              options={reportStreamOptions}
              placeholder="选择发现异常的数据流"
              value={reportStreamRef || undefined}
              onChange={setReportStreamRef}
            />
          </Form.Item>
          <Form.Item label="问题类型" required>
            <Select
              aria-label="问题类型"
              disabled={reportPending}
              options={[...dataIssueTypeOptions]}
              value={reportIssueType}
              onChange={setReportIssueType}
            />
          </Form.Item>
          <Form.Item label="严重程度" required>
            <Select
              aria-label="严重程度"
              disabled={reportPending}
              options={[...dataIssueSeverityOptions]}
              value={reportSeverity}
              onChange={setReportSeverity}
            />
          </Form.Item>
          <Form.Item label="问题说明" required>
            <Input.TextArea
              aria-label="问题说明"
              disabled={reportPending}
              maxLength={8192}
              rows={4}
              showCount
              placeholder="说明自动质检漏检了什么，以及在当前片段中观察到的证据…"
              value={reportNote}
              onChange={(event) => setReportNote(event.target.value)}
            />
          </Form.Item>
          {reportProblem ? (
            <Alert
              showIcon
              type="error"
              title="问题数据登记失败"
              description={`${reportProblem.message}${reportProblem.requestId ? `（请求 ID：${reportProblem.requestId}）` : ""}`}
            />
          ) : null}
          <div className={styles.reportModalActions}>
            <button
              disabled={reportPending}
              type="button"
              onClick={() => setReportDialogOpen(false)}
            >
              取消
            </button>
            <button
              aria-busy={reportPending || undefined}
              className={styles.reportSubmitAction}
              disabled={
                reportPending ||
                !reportSelection ||
                !reportStreamRef ||
                !reportNote.trim()
              }
              type="submit"
            >
              {reportPending ? (
                <LoaderCircle
                  aria-hidden="true"
                  className={styles.loadingIcon}
                  size={15}
                />
              ) : (
                <CircleAlert aria-hidden="true" size={15} />
              )}
              登记到问题数据
            </button>
          </div>
        </Form>
      </Modal>
      <Modal
        centered
        closable={pending !== "save"}
        footer={
          <div className={styles.modeSwitchActions}>
            <button
              type="button"
              disabled={pending === "save"}
              onClick={() => setModeSwitchTarget(null)}
            >
              继续编辑
            </button>
            <button
              className={styles.discardSwitchAction}
              type="button"
              disabled={pending === "save"}
              onClick={discardAndSwitch}
            >
              放弃修改
            </button>
            <button
              aria-busy={pending === "save" || undefined}
              className={styles.saveSwitchAction}
              type="button"
              disabled={pending === "save" || !permissions.canSave}
              onClick={saveAndSwitch}
            >
              {pending === "save" ? (
                <LoaderCircle
                  aria-hidden="true"
                  className={styles.loadingIcon}
                  size={15}
                />
              ) : null}
              保存后切换
            </button>
          </div>
        }
        keyboard={pending !== "save"}
        mask={{ closable: false }}
        open={modeSwitchTarget !== null}
        title="有未保存的修改"
        onCancel={() => {
          if (pending !== "save") setModeSwitchTarget(null);
        }}
      >
        <p>
          离开标注模式前请选择如何处理当前修改。取消后会保留当前模式和全部草稿内容。
        </p>
      </Modal>
      <DangerousActionDialog
        confirmLabel={
          dialogDecision === "SAVE"
            ? "确认保存修改"
            : dialogDecision === "SUBMIT"
              ? "确认提交审核"
              : dialogDecision === "RESTORE"
                ? `确认回退为 r${restoreTargetRevision ?? "?"}`
                : dialogDecision
                  ? "确认提交审核"
                  : "确认"
        }
        impact={
          dialogDecision === "SAVE"
            ? [
                "当前多级 Tag、区间、属性与数据排除/恢复操作将写入新的不可变标注修订",
                "原修订继续保留，可在数据修订历史中追溯",
              ]
            : dialogDecision === "SUBMIT"
              ? [
                  "当前已保存修订将生成固定提交版本",
                  "提交后进入审核流程，可能无法继续编辑",
                ]
              : dialogDecision === "RESTORE"
                ? [
                    `当前多级 Tag 与区间将按历史 r${restoreTargetRevision ?? "?"} 生成新的不可变数据修订`,
                    "原修订、提交版本与审核记录继续保留；不会覆盖历史数据",
                  ]
                : dialogDecision === "APPROVE"
                  ? [
                      "创建不可变审核通过记录",
                      "固定该 Revision、提交与标签结构版本",
                    ]
                  : ["创建不可变审核决定", "原提交版本和全部审核历史仍保留"]
        }
        open={dialogDecision !== null}
        pending={pending !== null}
        stableResourceId={props.bundle.task.task_id}
        title={
          dialogDecision === "SAVE"
            ? "保存标注修改"
            : dialogDecision === "SUBMIT"
              ? "确认提交审核？"
              : dialogDecision === "RESTORE"
                ? "回退标注数据修订"
                : dialogDecision
                  ? "提交审核"
                  : "确认操作"
        }
        onCancel={() => {
          if (dialogDecision === "SUBMIT")
            setPreSubmitCheckState(idlePreSubmitCheck);
          setDialogDecision(null);
        }}
        onConfirm={confirmDecision}
      >
        {dialogDecision === "SUBMIT" && preSubmitCheck.status === "passed" ? (
          <>
            <p className={styles.preSubmitPassed} role="status">
              <ShieldCheck aria-hidden="true" size={15} />
              检查已通过
            </p>
            <p>
              提交后会生成一个新的 Episode 版本并进入审核；草稿保存本身不会增加 Episode 版本号。
            </p>
          </>
        ) : null}
        {dialogDecision &&
        dialogDecision !== "SAVE" &&
        dialogDecision !== "SUBMIT" &&
        dialogDecision !== "RESTORE" ? (
          <p>
            审核基于 Episode v
            {resolveReviewSubmission(props.bundle)?.episode_version ?? "不可用"}（提交{" "}
            {resolveReviewSubmission(props.bundle)?.submission_id ?? "不可用"}）。
          </p>
        ) : null}
        {dialogDecision === "APPROVE" ? (
          <p className={styles.reviewOutcomeSummary} data-outcome="approved">
            未标记问题。提交后任务将进入“标注完成”。
          </p>
        ) : dialogDecision === "NEEDS_REVISION" ? (
          <p className={styles.reviewOutcomeSummary} data-outcome="revision">
            已标记 {reviewIssueRows.length}{" "}
            处问题。提交后任务将进入“待修改”，修改并重新提交后可再次审核。
          </p>
        ) : null}
      </DangerousActionDialog>
    </div>
  );
}
