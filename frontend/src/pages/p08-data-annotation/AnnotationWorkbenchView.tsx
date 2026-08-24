import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, JSX, ReactNode } from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleAlert,
  Database,
  GitCompareArrows,
  Link2,
  ListTree,
  LoaderCircle,
  LockKeyhole,
  Plus,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import {
  DataVisualizationWorkbench,
  WorkbenchCollectionPanel,
} from "../../features/viewer";
import type {
  DomainError,
  ViewerPanelRenderer,
  ViewerTimelineSelection,
} from "../../features/viewer";
import { createPlaybackClock } from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import { DangerousActionDialog } from "../../features/annotation";
import { EntityDrawer } from "../../shared/ui/layout/EntityDrawer";
import {
  buildRuntimeWorkbenchAdapter,
  createClientMutationId,
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
import {
  buildTagSchemaIndex,
  emptyAttributesForRow,
  evaluateAnnotationTags,
  inheritedAttributes,
} from "./tag-validation";
import type { TagReviewCheckResult, TagSchemaTreeRow } from "./tag-validation";
import "./p08.css";
import styles from "./workbench.module.css";

export interface AnnotationWorkbenchPermissions {
  readonly canEdit: boolean;
  readonly canSave: boolean;
  readonly canSubmit: boolean;
  readonly canReview: boolean;
  readonly readOnlyReason?: string;
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
  readonly autoAnnotationPanel?: ReactNode;
  readonly externalError?: unknown;
  readonly onTagsChange: (tags: readonly RuntimeAnnotationTag[]) => void;
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
}

interface StepSelection {
  readonly startStep: number;
  readonly endStep: number;
}

type PendingAction =
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
type AnnotationSurface = "view" | "annotation" | "revisions";
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

function displayRevisionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function tagIdentity(): string {
  return createClientMutationId("tag").replaceAll(":", "-");
}

function selectedTagById(
  tags: readonly RuntimeAnnotationTag[],
  id: string | null,
): RuntimeAnnotationTag | null {
  return tags.find((tag) => tag.annotation_id === id) ?? tags[0] ?? null;
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

function TagTree(props: {
  readonly rows: readonly TagSchemaTreeRow[];
  readonly activeTagId: string | null;
  readonly onSelect: (tagId: string) => void;
}): JSX.Element {
  return (
    <div
      className={styles.tagTree}
      role="tree"
      aria-label="多级 Tag Schema 层级"
    >
      {props.rows.map((row) => (
        <button
          aria-level={row.depth + 1}
          aria-selected={row.node.tag_id === props.activeTagId}
          className={styles.treeItem}
          key={row.node.tag_id}
          role="treeitem"
          style={{ "--tag-depth": row.depth } as CSSProperties}
          type="button"
          onClick={() => props.onSelect(row.node.tag_id)}
        >
          <span aria-hidden="true" className={styles.treeBranch}>
            {row.hasChildren ? "├" : "└"}
          </span>
          <span>{row.node.display_name}</span>
          <code>{row.node.code}</code>
        </button>
      ))}
    </div>
  );
}

function AttributeField(props: {
  readonly definition: TagSchemaTreeRow["node"]["attributes"][number];
  readonly value: string | number | boolean | undefined;
  readonly disabled: boolean;
  readonly onChange: (value: string | number | boolean) => void;
}): JSX.Element {
  const { definition } = props;
  const id = `tag-attribute-${definition.key}`;
  if (definition.value_type === "BOOLEAN") {
    return (
      <label className={styles.checkboxField} htmlFor={id}>
        <input
          checked={props.value === true}
          disabled={props.disabled}
          id={id}
          type="checkbox"
          onChange={(event) => props.onChange(event.target.checked)}
        />
        <span>
          {definition.display_name}
          {definition.required ? " *" : ""}
        </span>
      </label>
    );
  }
  if (definition.value_type === "ENUM") {
    return (
      <label htmlFor={id}>
        <span>
          {definition.display_name}
          {definition.required ? " *" : ""}
        </span>
        <select
          disabled={props.disabled}
          id={id}
          name={id}
          value={typeof props.value === "string" ? props.value : ""}
          onChange={(event) => props.onChange(event.target.value)}
        >
          <option value="">请选择</option>
          {definition.enum_values.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
    );
  }
  const numeric =
    definition.value_type === "INTEGER" || definition.value_type === "NUMBER";
  return (
    <label htmlFor={id}>
      <span>
        {definition.display_name}
        {definition.required ? " *" : ""}
      </span>
      <input
        autoComplete="off"
        disabled={props.disabled}
        id={id}
        name={id}
        type={numeric ? "number" : "text"}
        value={
          typeof props.value === "string" || typeof props.value === "number"
            ? props.value
            : ""
        }
        onChange={(event) =>
          props.onChange(
            numeric ? Number(event.target.value) : event.target.value,
          )
        }
      />
    </label>
  );
}

function TagEditorInspector(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly selection: StepSelection | null;
  readonly disabled: boolean;
  readonly onChange: (tags: readonly RuntimeAnnotationTag[]) => void;
}): JSX.Element {
  const index = useMemo(
    () => buildTagSchemaIndex(props.bundle.schema),
    [props.bundle.schema],
  );
  const [activeTagId, setActiveTagId] = useState<string | null>(
    () => props.tags[0]?.tag_id ?? index.rows[0]?.node.tag_id ?? null,
  );
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<
    string | null
  >(() => props.tags[0]?.annotation_id ?? null);
  const selectedTag = selectedTagById(props.tags, selectedAnnotationId);
  const activeRow = activeTagId ? (index.byId.get(activeTagId) ?? null) : null;
  const selectedRow = selectedTag
    ? (index.byId.get(selectedTag.tag_id) ?? null)
    : null;
  useEffect(() => {
    if (selectedTag && selectedAnnotationId !== selectedTag.annotation_id)
      setSelectedAnnotationId(selectedTag.annotation_id);
  }, [selectedAnnotationId, selectedTag]);

  const replaceSelected = (next: RuntimeAnnotationTag) => {
    props.onChange(
      props.tags.map((tag) =>
        tag.annotation_id === next.annotation_id ? next : tag,
      ),
    );
  };
  const addInterval = () => {
    if (!activeRow || props.disabled) return;
    const boundary = Math.max(1, props.bundle.task.base_step_count ?? 1);
    const startStep = Math.max(
      0,
      Math.min(props.selection?.startStep ?? 0, boundary - 1),
    );
    const endStep = Math.max(
      startStep + 1,
      Math.min(props.selection?.endStep ?? startStep + 1, boundary),
    );
    const relationConstraint =
      props.bundle.schema.document.object_relations.find((constraint) =>
        constraint.source_tag_ids.includes(activeRow.node.tag_id),
      );
    const next: RuntimeAnnotationTag = {
      annotation_id: tagIdentity(),
      tag_id: activeRow.node.tag_id,
      path: [...activeRow.path],
      start_step: startStep,
      end_step: endStep,
      attributes: emptyAttributesForRow(activeRow, index),
      relations: relationConstraint
        ? [
            {
              relation_type: relationConstraint.relation_type,
              target: {
                object_id: "",
                object_type: relationConstraint.target_object_types[0] ?? "",
              },
            },
          ]
        : [],
      subject: null,
    };
    props.onChange([...props.tags, next]);
    setSelectedAnnotationId(next.annotation_id);
  };
  const deleteSelected = () => {
    if (!selectedTag || props.disabled) return;
    props.onChange(
      props.tags.filter(
        (tag) => tag.annotation_id !== selectedTag.annotation_id,
      ),
    );
    setSelectedAnnotationId(null);
  };
  const relationConstraint = selectedTag
    ? props.bundle.schema.document.object_relations.find((constraint) =>
        constraint.source_tag_ids.includes(selectedTag.tag_id),
      )
    : undefined;
  const relation = selectedTag?.relations.find(
    (item) => item.relation_type === relationConstraint?.relation_type,
  );

  return (
    <section
      className={styles.inspectorPanel}
      aria-labelledby="tag-editor-title"
    >
      <header className={styles.inspectorHeader}>
        <span>
          <ListTree aria-hidden="true" size={15} />
        </span>
        <div>
          <h2 id="tag-editor-title">多级 Tag 工具</h2>
          <small>{props.bundle.schema.name}</small>
        </div>
        <em>
          v{props.bundle.schema.version} · {props.bundle.schema.status}
        </em>
      </header>
      <div className={styles.inspectorScroll}>
        {index.errors.length ? (
          <div className={styles.inlineError} role="alert">
            <CircleAlert aria-hidden="true" size={15} />
            <span>
              <strong>Schema 层级不可安全编辑</strong>
              {index.errors.join("；")}
            </span>
          </div>
        ) : null}
        <section className={styles.panelSection}>
          <h3>标签路径</h3>
          <PathCrumbs path={activeRow?.displayPath ?? ["请选择层级"]} />
          <TagTree
            rows={index.rows}
            activeTagId={activeTagId}
            onSelect={setActiveTagId}
          />
          <button
            className={styles.addTagButton}
            disabled={!activeRow || props.disabled || index.errors.length > 0}
            type="button"
            onClick={addInterval}
          >
            <Plus aria-hidden="true" size={14} />
            添加所选 Tag 区间
          </button>
        </section>
        <section className={styles.panelSection}>
          <h3>
            标注区间 <span>共 {props.tags.length} 项</span>
          </h3>
          {props.tags.length ? (
            <div className={styles.intervalList}>
              {props.tags.map((tag) => {
                const row = index.byId.get(tag.tag_id);
                return (
                  <button
                    aria-current={
                      tag.annotation_id === selectedTag?.annotation_id
                        ? "true"
                        : undefined
                    }
                    key={tag.annotation_id}
                    type="button"
                    onClick={() => {
                      setSelectedAnnotationId(tag.annotation_id);
                      setActiveTagId(tag.tag_id);
                    }}
                  >
                    <strong>
                      {row?.displayPath.join(" / ") ?? tag.path.join(" / ")}
                    </strong>
                    <code>
                      [{tag.start_step}, {tag.end_step})
                    </code>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className={styles.emptyText}>
              尚未添加 Tag 区间。先在共享时间轴选择范围，再选择层级。
            </p>
          )}
        </section>
        {selectedTag && selectedRow ? (
          <section className={styles.panelSection}>
            <div className={styles.sectionTitleRow}>
              <h3>属性与关系</h3>
              <button
                aria-label="删除当前 Tag 区间"
                disabled={props.disabled}
                type="button"
                onClick={deleteSelected}
              >
                <Trash2 aria-hidden="true" size={14} />
              </button>
            </div>
            <PathCrumbs path={selectedRow.displayPath} />
            <div className={styles.fieldGrid}>
              <label htmlFor="tag-start-step">
                <span>开始步 *</span>
                <input
                  autoComplete="off"
                  disabled={props.disabled}
                  id="tag-start-step"
                  min={0}
                  name="tag-start-step"
                  type="number"
                  value={selectedTag.start_step}
                  onChange={(event) =>
                    replaceSelected({
                      ...selectedTag,
                      start_step: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label htmlFor="tag-end-step">
                <span>结束步（开区间）*</span>
                <input
                  autoComplete="off"
                  disabled={props.disabled}
                  id="tag-end-step"
                  min={1}
                  name="tag-end-step"
                  type="number"
                  value={selectedTag.end_step}
                  onChange={(event) =>
                    replaceSelected({
                      ...selectedTag,
                      end_step: Number(event.target.value),
                    })
                  }
                />
              </label>
              {inheritedAttributes(selectedRow, index).map((definition) => (
                <AttributeField
                  definition={definition}
                  disabled={props.disabled}
                  key={definition.key}
                  value={selectedTag.attributes?.[definition.key]}
                  onChange={(value) =>
                    replaceSelected({
                      ...selectedTag,
                      attributes: {
                        ...selectedTag.attributes,
                        [definition.key]: value,
                      },
                    })
                  }
                />
              ))}
            </div>
            {relationConstraint ? (
              <div className={styles.relationEditor}>
                <h4>
                  <Link2 aria-hidden="true" size={13} />
                  对象关系 · {relationConstraint.relation_type}
                </h4>
                <label htmlFor="tag-object-type">
                  <span>对象类型{relationConstraint.required ? " *" : ""}</span>
                  <select
                    disabled={props.disabled}
                    id="tag-object-type"
                    name="tag-object-type"
                    value={relation?.target.object_type ?? ""}
                    onChange={(event) =>
                      replaceSelected({
                        ...selectedTag,
                        relations: [
                          {
                            relation_type: relationConstraint.relation_type,
                            target: {
                              object_id: relation?.target.object_id ?? "",
                              object_type: event.target.value,
                            },
                          },
                        ],
                      })
                    }
                  >
                    <option value="">请选择</option>
                    {relationConstraint.target_object_types.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </label>
                <label htmlFor="tag-object-id">
                  <span>对象 ID{relationConstraint.required ? " *" : ""}</span>
                  <input
                    autoComplete="off"
                    disabled={props.disabled}
                    id="tag-object-id"
                    name="tag-object-id"
                    value={relation?.target.object_id ?? ""}
                    onChange={(event) =>
                      replaceSelected({
                        ...selectedTag,
                        relations: [
                          {
                            relation_type: relationConstraint.relation_type,
                            target: {
                              object_id: event.target.value,
                              object_type:
                                relation?.target.object_type ??
                                relationConstraint.target_object_types[0] ??
                                "",
                            },
                          },
                        ],
                      })
                    }
                  />
                </label>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </section>
  );
}

function useCurrentStep(clock: ReturnType<typeof createPlaybackClock>): number {
  const [step, setStep] = useState(() => timelineNsToStep(clock.currentNs()));
  useEffect(
    () => clock.subscribe((value) => setStep(timelineNsToStep(value))),
    [clock],
  );
  return step;
}

function ReviewInspector(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly clock: ReturnType<typeof createPlaybackClock>;
  readonly comment: string;
  readonly onCommentChange: (value: string) => void;
}): JSX.Element {
  const currentStep = useCurrentStep(props.clock);
  const schemaIndex = useMemo(
    () => buildTagSchemaIndex(props.bundle.schema),
    [props.bundle.schema],
  );
  const checks = evaluateAnnotationTags({
    task: props.bundle.task,
    schema: props.bundle.schema,
    tags: props.tags,
  });
  const currentTag =
    props.tags.find(
      (tag) => tag.start_step <= currentStep && currentStep < tag.end_step,
    ) ??
    props.tags[0] ??
    null;
  const currentRow = currentTag
    ? (schemaIndex.byId.get(currentTag.tag_id) ?? null)
    : null;
  const original = resolveOriginalRevision(props.bundle);
  const revised = resolveReviewRevision(props.bundle);
  const originalTag = currentTag
    ? (original?.tags.find(
        (tag) => tag.annotation_id === currentTag.annotation_id,
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
          <small>修订 v{revised?.revision ?? "—"} · 提交快照</small>
        </div>
        <em>
          {props.bundle.schema.schema_id} v{props.bundle.schema.version}
        </em>
      </header>
      <div className={styles.inspectorScroll}>
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
          {currentTag && currentRow ? (
            <>
              <PathCrumbs path={currentRow.displayPath} />
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
                {originalTag?.path.join(" / ") ?? "无对应项"}
              </span>
              <span role="cell">{currentTag?.path.join(" / ") ?? "无"}</span>
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
            <span>审核意见（要求修改或拒绝时必填）</span>
            <textarea
              autoComplete="off"
              id="tag-review-comment"
              maxLength={500}
              name="tag-review-comment"
              placeholder="说明需修订的 Tag、区间或关系…"
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
              <dt>Tag Schema</dt>
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

function ActionDock(props: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly mode: AnnotationWorkbenchMode;
  readonly surface: AnnotationSurface;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly dirty: boolean;
  readonly permissions: AnnotationWorkbenchPermissions;
  readonly reviewChecks: readonly TagReviewCheckResult[];
  readonly preSubmitCheck: PreSubmitCheckState;
  readonly dialogOpen: boolean;
  readonly pending: PendingAction;
  readonly reviewComment: string;
  readonly restoreCandidates: readonly {
    readonly revision: number;
    readonly origin: string;
  }[];
  readonly restoreTargetRevision: number | null;
  readonly autoAnnotationPanel?: ReactNode;
  readonly onOpenSave: () => void;
  readonly onOpenSubmit: () => void;
  readonly onOpenReview: (decision: RuntimeReviewDecision) => void;
  readonly onRestoreTargetChange: (revision: number | null) => void;
  readonly onOpenRestore?: (targetRevision: number) => void;
  readonly onOpenRevisionLedger?: () => void;
}): JSX.Element {
  const reviewBlocked = props.reviewChecks.some(
    (check) => check.status === "FAIL",
  );
  const tag = props.tags[0] ?? null;
  const schemaIndex = buildTagSchemaIndex(props.bundle.schema);
  const displayPath = tag
    ? (schemaIndex.byId.get(tag.tag_id)?.displayPath ?? tag.path)
    : [];
  const summary = (
    <section
      className={styles.dockSummary}
      aria-label={
        props.mode === "tag-review" ? "提交快照概要" : "当前 Tag 概要"
      }
    >
      <h3>{props.mode === "tag-review" ? "提交快照概要" : "当前 Tag 概要"}</h3>
      {tag ? (
        <>
          <PathCrumbs path={displayPath} />
          <dl>
            <div>
              <dt>Schema</dt>
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
  if (props.mode === "tag-review") {
    return (
      <section className={styles.actionDock} aria-label="Tag 审核决定">
        {summary}
        <div className={styles.reviewSecondaryActions}>
          <button
            disabled={
              !props.permissions.canReview ||
              props.pending !== null ||
              !props.reviewComment.trim()
            }
            type="button"
            onClick={() => props.onOpenReview("NEEDS_REVISION")}
          >
            要求修改
          </button>
          <button
            className={styles.rejectButton}
            disabled={
              !props.permissions.canReview ||
              props.pending !== null ||
              !props.reviewComment.trim()
            }
            type="button"
            onClick={() => props.onOpenReview("REJECT")}
          >
            拒绝
          </button>
        </div>
        <button
          className={styles.primaryAction}
          disabled={
            !props.permissions.canReview ||
            props.pending !== null ||
            reviewBlocked
          }
          type="button"
          onClick={() => props.onOpenReview("APPROVE")}
        >
          <ShieldCheck aria-hidden="true" size={15} />
          审核通过
        </button>
        <p>
          <LockKeyhole aria-hidden="true" size={13} />
          只对固定提交快照创建不可变审核记录。
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
      </section>
    );
  }
  if (props.surface === "revisions") {
    return (
      <section className={styles.actionDock} aria-label="数据修订动作">
        {summary}
        {props.onOpenRestore && props.restoreCandidates.length > 0 ? (
          <div className={styles.revisionRestoreControl}>
            <label htmlFor="annotation-restore-revision">
              <span>回退目标修订</span>
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
            <button
              disabled={
                !props.permissions.canSave ||
                props.dirty ||
                props.pending !== null ||
                props.restoreTargetRevision === null
              }
              type="button"
              onClick={() => {
                if (props.restoreTargetRevision !== null)
                  props.onOpenRestore?.(props.restoreTargetRevision);
              }}
            >
              <RotateCcw aria-hidden="true" size={14} />
              {props.pending === "restore" ? "回退中…" : "回退为该修订"}
            </button>
            <small>回退会新建数据修订，不会覆盖任何历史版本。</small>
          </div>
        ) : null}
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
      {props.autoAnnotationPanel}
      {props.preSubmitCheck.status !== "idle" &&
      props.preSubmitCheck.status !== "passed" ? (
        <PreSubmitCheckPanel state={props.preSubmitCheck} />
      ) : null}
      <button
        disabled={
          !props.permissions.canSave ||
          !props.dirty ||
          props.pending !== null ||
          props.dialogOpen
        }
        type="button"
        onClick={props.onOpenSave}
      >
        <Save aria-hidden="true" size={15} />
        {props.pending === "save" ? "保存中…" : "保存修改"}
      </button>
      <button
        className={styles.primaryAction}
        disabled={
          !props.permissions.canSubmit ||
          props.dirty ||
          props.pending !== null ||
          props.dialogOpen
        }
        type="button"
        onClick={props.onOpenSubmit}
      >
        <Send aria-hidden="true" size={15} />
        {props.pending === "precheck" ? "检查中…" : "提交审核"}
      </button>
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
      message: `${error.message}${error.problemCode ? `（问题代码：${error.problemCode}）` : ""}${error.retryable ? "；服务端允许重试。" : ""}`,
      ...(error.requestId ? { requestId: error.requestId } : {}),
      conflict:
        error.code === "VERSION_CONFLICT" ||
        error.code === "PRECONDITION_FAILED",
    };
  }
  return {
    message: error instanceof Error ? error.message : "操作未完成。",
    conflict: false,
  };
}

export function AnnotationWorkbenchView(
  props: AnnotationWorkbenchViewProps,
): JSX.Element {
  const [selection, setSelection] = useState<StepSelection | null>(null);
  const [selectedCameraId, setSelectedCameraId] = useState<string | undefined>(
    props.bundle.manifest?.cameras[0]?.camera_id,
  );
  const [reviewComment, setReviewComment] = useState("");
  const [pending, setPending] = useState<PendingAction>(null);
  const [preSubmitCheckState, setPreSubmitCheckState] =
    useState<PreSubmitCheckState>(idlePreSubmitCheck);
  const [localError, setLocalError] = useState<unknown>(null);
  const [dialogDecision, setDialogDecision] = useState<DialogDecision>(null);
  const [dataInfoOpen, setDataInfoOpen] = useState(false);
  const dataInfoTriggerRef = useRef<HTMLButtonElement>(null);
  const [surface, setSurface] = useState<AnnotationSurface>("annotation");
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
        canEdit: false,
        canSave: false,
        canSubmit: false,
        canReview: false,
        readOnlyReason: "并发版本已变化；刷新真实任务后才能继续写入。",
      }
    : props.permissions;
  const stepCount = Math.max(1, props.bundle.task.base_step_count ?? 1);
  const clock = useMemo(
    () =>
      createPlaybackClock({ startNs: "0", endNs: stepToTimelineNs(stepCount) }),
    [props.bundle.task.task_id, stepCount],
  );
  useEffect(() => () => clock.dispose(), [clock]);
  const reviewChecks =
    props.mode === "tag-review"
      ? evaluateAnnotationTags({
          task: props.bundle.task,
          schema: props.bundle.schema,
          tags: props.tags,
        })
      : [];
  const restoreCandidates = props.bundle.history.revisions
    .filter(
      (revision) => revision.revision < props.bundle.task.current_revision,
    )
    .toSorted((left, right) => right.revision - left.revision);
  const timelineSelection = useMemo<ViewerTimelineSelection | undefined>(
    () =>
      selection
        ? {
            startNs: stepToTimelineNs(selection.startStep),
            endNs: stepToTimelineNs(selection.endStep),
            label: `第 ${selection.startStep}–${selection.endStep} 步`,
          }
        : undefined,
    [selection],
  );
  const onTimeRangeSelect = useCallback(
    (startNs: string, endNs: string) =>
      setSelection({
        startStep: timelineNsToStep(startNs),
        endStep: Math.max(
          timelineNsToStep(startNs) + 1,
          timelineNsToStep(endNs),
        ),
      }),
    [],
  );
  const onResourceError = useCallback((error: DomainError) => {
    const message = `${error.message}${error.requestId ? `（${error.requestId}）` : ""}`;
    setResourceErrors((current) =>
      current.includes(message) ? current : [...current, message],
    );
  }, []);
  const adapter = useMemo(
    () =>
      buildRuntimeWorkbenchAdapter({
        bundle: props.bundle,
        scope: props.scope,
        mode: props.mode,
        clock,
        tags: props.tags,
        selectedCameraId,
        readOnly:
          props.mode === "tag-review" ||
          surface !== "annotation" ||
          !permissions.canEdit,
        ...(props.onSelectTask && pending === null
          ? { onSelectTask: props.onSelectTask }
          : {}),
        ...(timelineSelection ? { timelineSelection } : {}),
        ...(!permissions.canEdit ||
        props.mode === "tag-review" ||
        surface !== "annotation"
          ? {}
          : { onTimeRangeSelect }),
        onResourceError,
      }),
    [
      clock,
      onResourceError,
      onTimeRangeSelect,
      permissions.canEdit,
      pending,
      props.bundle,
      props.mode,
      props.onSelectTask,
      props.scope,
      props.tags,
      selectedCameraId,
      surface,
      timelineSelection,
    ],
  );
  const invoke = async (action: PendingAction, task: () => Promise<void>) => {
    if (!action || pending || actionInFlightRef.current) return;
    actionInFlightRef.current = true;
    setPending(action);
    setLocalError(null);
    try {
      await task();
    } catch (error) {
      setLocalError(error);
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
    if (decision === "SAVE") void invoke("save", props.onSave);
    else if (decision === "SUBMIT") {
      if (preSubmitCheck.status === "passed") {
        setPreSubmitCheckState(idlePreSubmitCheck);
        void invoke("submit", props.onSubmit);
      }
    } else if (decision === "RESTORE") {
      if (restoreTargetRevision !== null && props.onRestoreRevision)
        void invoke(
          "restore",
          () =>
            props.onRestoreRevision?.(restoreTargetRevision) ??
            Promise.resolve(),
        );
    } else if (decision)
      void invoke(decision, () =>
        props.onReview(decision, reviewComment.trim()),
      );
  };
  const cameras = props.bundle.manifest?.cameras ?? [];
  const mediaHeader = (): ReactNode => (
    <div className={styles.modeStrip}>
      <nav aria-label="数据标注模式">
        <button
          aria-current={
            props.mode === "annotation" && surface === "view"
              ? "page"
              : undefined
          }
          disabled={pending !== null || props.mode === "tag-review"}
          type="button"
          onClick={() => setSurface("view")}
        >
          查看
        </button>
        <button
          aria-current={
            props.mode === "annotation" && surface === "annotation"
              ? "page"
              : undefined
          }
          disabled={pending !== null}
          type="button"
          onClick={() => {
            setSurface("annotation");
            props.onSwitchMode?.("annotation");
          }}
        >
          标注
        </button>
        <button
          aria-current={props.mode === "tag-review" ? "page" : undefined}
          disabled={pending !== null}
          type="button"
          onClick={() => props.onSwitchMode?.("tag-review")}
        >
          Tag 审核
        </button>
        <button
          aria-current={
            props.mode === "annotation" && surface === "revisions"
              ? "page"
              : undefined
          }
          disabled={pending !== null || props.mode === "tag-review"}
          type="button"
          onClick={() => setSurface("revisions")}
        >
          数据修订
        </button>
      </nav>
      <div className={styles.modeStripTools}>
        {props.mode === "tag-review" ? (
          <label htmlFor="review-camera-select">
            <span>Manifest {cameras.length} 路 · 当前相机</span>
            <select
              id="review-camera-select"
              name="review-camera-select"
              value={selectedCameraId ?? ""}
              onChange={(event) => setSelectedCameraId(event.target.value)}
            >
              {cameras.map((camera) => (
                <option key={camera.camera_id} value={camera.camera_id}>
                  {camera.camera_id}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span>自动发现 {cameras.length} 路相机 · 同一共享时间轴</span>
        )}
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
    <main className={styles.page} data-p08-mode={props.mode}>
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
      {resourceErrors.length ? (
        <section className={styles.resourceNotice} role="status">
          <AlertTriangle aria-hidden="true" size={14} />
          <span>部分预览加载失败：{resourceErrors.join("；")}</span>
        </section>
      ) : null}
      <div className={styles.workbenchFrame}>
        <DataVisualizationWorkbench
          adapter={adapter}
          showNavigation={false}
          slots={{
            mediaHeader,
            inspector: () =>
              props.mode === "tag-review" ? (
                <ReviewInspector
                  bundle={props.bundle}
                  clock={clock}
                  comment={reviewComment}
                  tags={props.tags}
                  onCommentChange={setReviewComment}
                />
              ) : surface === "view" ? (
                <DataViewInspector bundle={props.bundle} tags={props.tags} />
              ) : surface === "revisions" ? (
                <RevisionHistoryInspector
                  bundle={props.bundle}
                  disabled={pending !== null || !permissions.canSave}
                  selectedRevision={restoreTargetRevision}
                  onSelectRevision={setRestoreTargetRevision}
                />
              ) : (
                <TagEditorInspector
                  bundle={props.bundle}
                  disabled={!permissions.canEdit || pending !== null}
                  selection={selection}
                  tags={props.tags}
                  onChange={props.onTagsChange}
                />
              ),
            actionDock: () => (
              <ActionDock
                bundle={props.bundle}
                autoAnnotationPanel={props.autoAnnotationPanel}
                dialogOpen={dialogDecision !== null}
                dirty={props.dirty}
                mode={props.mode}
                surface={surface}
                pending={pending}
                permissions={permissions}
                preSubmitCheck={preSubmitCheck}
                reviewComment={reviewComment}
                reviewChecks={reviewChecks}
                restoreCandidates={restoreCandidates}
                restoreTargetRevision={restoreTargetRevision}
                tags={props.tags}
                onOpenReview={setDialogDecision}
                onOpenRevisionLedger={props.onOpenRevisions}
                onOpenRestore={(targetRevision) => {
                  setRestoreTargetRevision(targetRevision);
                  setDialogDecision("RESTORE");
                }}
                onOpenSubmit={() => void runPreSubmitCheck()}
                onOpenSave={() => {
                  setPreSubmitCheckState(idlePreSubmitCheck);
                  setDialogDecision("SAVE");
                }}
                onRestoreTargetChange={setRestoreTargetRevision}
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
      <DangerousActionDialog
        confirmLabel={
          dialogDecision === "SAVE"
            ? "确认保存修改"
            : dialogDecision === "SUBMIT"
              ? "确认提交审核"
              : dialogDecision === "RESTORE"
                ? `确认回退为 r${restoreTargetRevision ?? "?"}`
                : dialogDecision
                  ? reviewDecisionLabels[dialogDecision]
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
                  "保存后的当前修订将生成不可变提交快照",
                  "提交后进入 Tag 审核，当前草稿不再直接修改该快照",
                ]
              : dialogDecision === "RESTORE"
                ? [
                    `当前多级 Tag 与区间将按历史 r${restoreTargetRevision ?? "?"} 生成新的不可变数据修订`,
                    "原修订、提交快照与审核记录继续保留；不会覆盖历史数据",
                  ]
                : dialogDecision === "APPROVE"
                  ? [
                      "创建不可变审核通过记录",
                      "固定该 Revision、提交与 Tag Schema 版本",
                    ]
                  : ["创建不可变审核决定", "原提交快照和全部审核历史仍保留"]
        }
        open={dialogDecision !== null}
        pending={pending !== null}
        stableResourceId={props.bundle.task.task_id}
        title={
          dialogDecision === "SAVE"
            ? "保存标注修改"
            : dialogDecision === "SUBMIT"
              ? "提交 Tag 审核"
              : dialogDecision === "RESTORE"
                ? "回退标注数据修订"
                : dialogDecision
                  ? reviewDecisionLabels[dialogDecision]
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
          <p className={styles.preSubmitPassed} role="status">
            <ShieldCheck aria-hidden="true" size={15} />
            检查已通过
          </p>
        ) : null}
        {dialogDecision &&
        dialogDecision !== "SAVE" &&
        dialogDecision !== "SUBMIT" &&
        dialogDecision !== "RESTORE" ? (
          <p>
            审核基于提交{" "}
            {resolveReviewSubmission(props.bundle)?.submission_id ?? "不可用"}。
          </p>
        ) : null}
      </DangerousActionDialog>
    </main>
  );
}
