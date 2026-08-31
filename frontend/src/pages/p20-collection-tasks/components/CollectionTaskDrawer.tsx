import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Skeleton,
  Space,
  TreeSelect,
  Typography,
} from "antd";
import type { InputRef } from "antd";
import { RefreshCw, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { isDomainError } from "../../../shared/api/domain-error";
import { PageState } from "../../../shared/ui/state/PageState";
import type { PageStateKind } from "../../../shared/ui/state/contracts";
import type {
  AssignableDataset,
  CollectionTask,
  CreateCollectionTask,
} from "../api";
import styles from "../styles.module.css";

interface TaskFormValues {
  name: string;
  datasetId: string;
  type: string;
  scenario: string;
  description: string;
  packageCount?: number | null;
  durationHours?: number | null;
  qualityThresholdPercent?: number | null;
}

export interface CollectionTaskDrawerProps {
  readonly mode: "create" | "edit";
  readonly projectId: string;
  readonly initialTask?: CollectionTask;
  readonly loading?: boolean;
  readonly pending?: boolean;
  readonly error?: unknown;
  readonly datasets?: readonly AssignableDataset[];
  readonly datasetsLoading?: boolean;
  readonly datasetError?: unknown;
  readonly onClose: () => void;
  readonly onReload?: () => void;
  readonly onReloadDatasets?: () => void;
  readonly onFormChanged?: () => void;
  readonly onSubmit: (command: CreateCollectionTask) => Promise<void>;
}

const NEW_DATASET_VALUE = "__new_dataset__";

interface DatasetTreeNode {
  title: string;
  value: string;
  selectable?: boolean;
  children?: DatasetTreeNode[];
}

function datasetTreeData(
  datasets: readonly AssignableDataset[],
  currentDatasetId?: string,
): DatasetTreeNode[] {
  const roots: DatasetTreeNode[] = [
    {
      title: "新建专属数据集（系统生成）",
      value: NEW_DATASET_VALUE,
    },
  ];
  const folders = new Map<string, DatasetTreeNode>();
  const sorted = [...datasets].sort((left, right) =>
    [...left.folderPath, left.name, left.datasetId]
      .join("/")
      .localeCompare(
        [...right.folderPath, right.name, right.datasetId].join("/"),
        "zh-CN",
      ),
  );

  for (const dataset of sorted) {
    let children = roots;
    const path: string[] = [];
    for (const segment of dataset.folderPath) {
      path.push(segment);
      const key = path.join("\u001f");
      let folder = folders.get(key);
      if (!folder) {
        folder = {
          title: segment,
          value: `__folder__:${key}`,
          selectable: false,
          children: [],
        };
        folders.set(key, folder);
        children.push(folder);
      }
      children = folder.children as DatasetTreeNode[];
    }
    children.push({
      title: `${dataset.name} · ${dataset.datasetId}`,
      value: dataset.datasetId,
    });
  }

  if (
    currentDatasetId &&
    !datasets.some((dataset) => dataset.datasetId === currentDatasetId)
  ) {
    roots.push({
      title: `当前关联 · ${currentDatasetId}`,
      value: currentDatasetId,
    });
  }
  return roots;
}

function initialValues(task: CollectionTask | undefined): TaskFormValues {
  return {
    name: task?.name ?? "",
    datasetId: task?.dataset_id ?? NEW_DATASET_VALUE,
    type: task?.type ?? "",
    scenario: task?.scenario ?? "",
    description: task?.description ?? "",
    packageCount: task?.target?.package_count ?? undefined,
    durationHours:
      task?.target?.duration_seconds == null
        ? undefined
        : task.target.duration_seconds / 3_600,
    qualityThresholdPercent:
      task?.quality_threshold == null
        ? undefined
        : task.quality_threshold * 100,
  };
}

function commandFromValues(values: TaskFormValues): CreateCollectionTask {
  const packageCount = values.packageCount;
  const durationHours = values.durationHours;
  const hasPackageTarget = typeof packageCount === "number";
  const hasDurationTarget = typeof durationHours === "number";
  return {
    name: values.name.trim(),
    ...(values.datasetId === NEW_DATASET_VALUE
      ? {}
      : { dataset_id: values.datasetId }),
    type: values.type.trim(),
    scenario: values.scenario.trim(),
    description: values.description.trim(),
    target:
      hasPackageTarget || hasDurationTarget
        ? {
            ...(hasPackageTarget ? { package_count: packageCount } : {}),
            ...(hasDurationTarget
              ? { duration_seconds: durationHours * 3_600 }
              : {}),
          }
        : null,
    quality_threshold:
      typeof values.qualityThresholdPercent === "number"
        ? values.qualityThresholdPercent / 100
        : null,
  };
}

function errorTitle(error: unknown): string {
  if (!isDomainError(error)) return "操作未完成";
  if (
    error.code === "VERSION_CONFLICT" ||
    error.code === "PRECONDITION_FAILED"
  ) {
    return "服务器事实已变化";
  }
  if (error.code === "RATE_LIMITED") return "请求频率受限";
  if (error.code === "VALIDATION_ERROR") return "请检查任务字段";
  if (error.problemCode === "COLLECTION_TASK_DATASET_NOT_ASSIGNABLE") {
    return "关联数据集不可用";
  }
  if (error.problemCode === "COLLECTION_TASK_DATASET_REASSIGNMENT_BLOCKED") {
    return "不能更改关联数据集";
  }
  if (error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED") {
    return "当前授权不允许此操作";
  }
  return "操作未完成";
}

function drawerErrorState(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "error";
  if (error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED") {
    return "forbidden";
  }
  if (error.code === "NOT_FOUND") return "not-found";
  if (error.code === "RATE_LIMITED") return "rate-limited";
  if (error.code === "NETWORK_ERROR") return "offline";
  if (error.code === "CONTRACT_MISMATCH") return "contract-mismatch";
  if (
    error.code === "VERSION_CONFLICT" ||
    error.code === "PRECONDITION_FAILED"
  ) {
    return "conflict";
  }
  return "error";
}

const fieldNames: Readonly<Record<string, keyof TaskFormValues>> = {
  name: "name",
  dataset_id: "datasetId",
  type: "type",
  scenario: "scenario",
  description: "description",
  package_count: "packageCount",
  duration_seconds: "durationHours",
  quality_threshold: "qualityThresholdPercent",
};

export function CollectionTaskDrawer({
  error,
  datasetError,
  datasets = [],
  datasetsLoading = false,
  initialTask,
  loading = false,
  mode,
  onClose,
  onFormChanged,
  onReload,
  onReloadDatasets,
  onSubmit,
  pending = false,
  projectId,
}: Readonly<CollectionTaskDrawerProps>) {
  const [form] = Form.useForm<TaskFormValues>();
  const [dirty, setDirty] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const headingId = useId();
  const firstInputRef = useRef<InputRef>(null);
  const submitLock = useRef(false);
  const datasetOptions = useMemo(
    () => datasetTreeData(datasets, initialTask?.dataset_id),
    [datasets, initialTask?.dataset_id],
  );

  useEffect(() => {
    form.resetFields();
    form.setFieldsValue(initialValues(initialTask));
    setDirty(false);
    submitLock.current = false;
  }, [
    form,
    initialTask?.collection_task_id,
    initialTask?.dataset_id,
    initialTask?.description,
    initialTask?.name,
    initialTask?.quality_threshold,
    initialTask?.scenario,
    initialTask?.target?.duration_seconds,
    initialTask?.target?.package_count,
    initialTask?.task_code,
    initialTask?.type,
    mode,
  ]);

  useEffect(() => {
    if (!error || !isDomainError(error) || error.fieldErrors.length === 0) {
      return;
    }
    const failures = error.fieldErrors.flatMap((fieldError) => {
      const segment = fieldError.path.split("/").filter(Boolean).at(-1);
      const name = segment ? fieldNames[segment] : undefined;
      return name ? [{ name, errors: [fieldError.message] }] : [];
    });
    if (failures.length > 0) form.setFields(failures);
  }, [error, form]);

  const requestClose = useCallback(() => {
    if (pending) return;
    if (dirty) setDiscardOpen(true);
    else onClose();
  }, [dirty, onClose, pending]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || discardOpen || pending) return;
      event.preventDefault();
      requestClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [discardOpen, pending, requestClose]);

  useEffect(() => {
    if (loading) return undefined;
    const timer = window.setTimeout(() => firstInputRef.current?.focus?.(), 0);
    return () => window.clearTimeout(timer);
  }, [loading]);

  const submit = async (values: TaskFormValues) => {
    if (submitLock.current || pending) return;
    submitLock.current = true;
    try {
      await onSubmit(commandFromValues(values));
      setDirty(false);
    } catch {
      // The parent mutation owns the visible RFC 9457 error state.
    } finally {
      submitLock.current = false;
    }
  };

  const conflict =
    isDomainError(error) &&
    (error.code === "VERSION_CONFLICT" || error.code === "PRECONDITION_FAILED");

  return (
    <div
      aria-labelledby={headingId}
      aria-modal="false"
      className={styles.editorDrawer}
      data-testid="collection-task-drawer"
      role="dialog"
    >
      <header className={styles.drawerHeader}>
        <div>
          <Typography.Title id={headingId} level={2}>
            {mode === "create" ? "新建采集任务" : "编辑采集任务"}
          </Typography.Title>
        </div>
        <Button
          aria-label="关闭任务抽屉"
          disabled={pending}
          icon={<X aria-hidden="true" size={18} />}
          type="text"
          onClick={requestClose}
        />
      </header>

      <div className={styles.drawerBody}>
        {loading ? (
          <>
            <Form form={form} hidden name="collection-task-editor-loading" />
            <Skeleton
              active
              aria-label="任务表单加载中"
              paragraph={{ rows: 9 }}
            />
          </>
        ) : mode === "edit" && !initialTask ? (
          <>
            <Form
              form={form}
              hidden
              name="collection-task-editor-unavailable"
            />
            <PageState
              action={<Button onClick={requestClose}>关闭抽屉</Button>}
              description={
                isDomainError(error)
                  ? error.message
                  : "任务详情未能加载，请重试后再编辑。"
              }
              label="任务详情"
              onRetry={onReload}
              requestId={isDomainError(error) ? error.requestId : null}
              state={drawerErrorState(error)}
            />
          </>
        ) : (
          <Form<TaskFormValues>
            form={form}
            initialValues={initialValues(initialTask)}
            layout="vertical"
            name="collection-task-editor"
            requiredMark
            onFinish={(values) => void submit(values)}
            onFinishFailed={({ errorFields }) => {
              const first = errorFields[0]?.name[0];
              if (first !== undefined)
                form.scrollToField(first, { focus: true });
            }}
            onValuesChange={() => {
              setDirty(true);
              onFormChanged?.();
            }}
          >
            <Form.Item label="项目" required>
              <Input
                aria-label="项目"
                autoComplete="off"
                name="project"
                readOnly
                value={projectId}
              />
              <span className={styles.fieldHint}>
                跟随顶部当前项目，不能跨项目创建。
              </span>
            </Form.Item>

            <Form.Item
              label="任务名称"
              name="name"
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: "请输入任务名称。",
                },
                { max: 200, message: "任务名称不能超过 200 个字符。" },
              ]}
            >
              <Input
                ref={firstInputRef}
                autoComplete="off"
                maxLength={200}
                name="name"
                placeholder="例如：透明件抓取采集…"
                showCount
              />
            </Form.Item>

            <Form.Item label="任务编号（自动生成）">
              <Input
                aria-label="任务编号（自动生成）"
                autoComplete="off"
                className={styles.codeInput}
                name="task_code"
                readOnly
                value={initialTask?.task_code ?? "保存后由系统生成"}
              />
            </Form.Item>

            {datasetError ? (
              <Alert
                action={
                  onReloadDatasets ? (
                    <Button size="small" onClick={onReloadDatasets}>
                      重试
                    </Button>
                  ) : undefined
                }
                showIcon
                title="数据集列表加载失败，仍可选择新建专属数据集。"
                type="warning"
              />
            ) : null}
            <Form.Item
              label="关联数据集"
              name="datasetId"
              rules={[{ required: true, message: "请选择关联数据集。" }]}
            >
              <TreeSelect
                aria-label="关联数据集"
                disabled={pending}
                loading={datasetsLoading}
                placeholder="选择已有数据集或新建专属数据集"
                showSearch
                treeData={datasetOptions}
                treeLine
                treeNodeFilterProp="title"
              />
            </Form.Item>
            <div className={styles.fieldHint}>
              目录节点用于分组；任务属于所选数据集的一部分。同一数据集可接收多个任务的数据。
              {mode === "edit" ? " 任务收到首个数据包后不能改关联。" : ""}
            </div>

            <Form.Item
              label="采集类型"
              name="type"
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: "请输入采集类型。",
                },
                { max: 100, message: "采集类型不能超过 100 个字符。" },
              ]}
            >
              <Input
                autoComplete="off"
                maxLength={100}
                name="type"
                placeholder="请输入项目约定的采集类型…"
              />
            </Form.Item>

            <Form.Item
              label="采集场景"
              name="scenario"
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: "请输入采集场景。",
                },
                { max: 200, message: "采集场景不能超过 200 个字符。" },
              ]}
            >
              <Input.TextArea
                autoComplete="off"
                maxLength={200}
                name="scenario"
                placeholder="描述环境、工位或物料条件…"
                rows={2}
                showCount
              />
            </Form.Item>

            <Form.Item
              label="任务描述"
              name="description"
              rules={[
                { max: 10_000, message: "任务描述不能超过 10,000 个字符。" },
              ]}
            >
              <Input.TextArea
                aria-label="任务描述"
                autoComplete="off"
                maxLength={10_000}
                name="description"
                placeholder="补充采集要求与验收说明…"
                rows={3}
                showCount
              />
            </Form.Item>

            <fieldset className={styles.targetFieldset}>
              <legend>采集目标（可选）</legend>
              <div className={styles.targetGrid}>
                <Form.Item
                  label="数据包数量"
                  name="packageCount"
                  rules={[
                    {
                      validator: async (_, value: unknown) => {
                        if (value == null || value === "") return;
                        if (typeof value === "number" && value > 0) return;
                        throw new Error("目标数据包数量必须大于 0。");
                      },
                    },
                  ]}
                >
                  <InputNumber
                    aria-label="目标数据包数量"
                    inputMode="numeric"
                    min={1}
                    name="package_count"
                    placeholder="未设置…"
                    precision={0}
                  />
                </Form.Item>
                <Form.Item
                  label="总时长（小时）"
                  name="durationHours"
                  rules={[
                    {
                      validator: async (_, value: unknown) => {
                        if (value == null || value === "") return;
                        if (typeof value === "number" && value > 0) return;
                        throw new Error("目标总时长必须大于 0。");
                      },
                    },
                  ]}
                >
                  <InputNumber
                    aria-label="目标总时长（小时）"
                    inputMode="decimal"
                    min={0.01}
                    name="duration_hours"
                    placeholder="未设置…"
                    precision={2}
                  />
                </Form.Item>
              </div>
              <span className={styles.fieldHint}>
                探索性任务可不设目标；已设置的目标必须大于
                0。目标达成只反映进度事实，不会自动关闭任务。
              </span>
            </fieldset>

            <Form.Item
              extra="可留空；填写 0–100，页面按百分比换算正式合同的 0–1 比率。"
              label="质量通过阈值（%）"
              name="qualityThresholdPercent"
              rules={[
                {
                  validator: async (_, value: unknown) => {
                    if (value == null || value === "") return;
                    if (
                      typeof value === "number" &&
                      value >= 0 &&
                      value <= 100
                    ) {
                      return;
                    }
                    throw new Error("质量通过阈值必须在 0–100 之间。");
                  },
                },
              ]}
            >
              <InputNumber
                aria-label="质量通过阈值（%）"
                inputMode="decimal"
                max={100}
                min={0}
                name="quality_threshold_percent"
                placeholder="未设置…"
                precision={2}
                suffix="%"
              />
            </Form.Item>

            {error ? (
              <Alert
                className={styles.formError}
                role="alert"
                showIcon
                title={errorTitle(error)}
                description={
                  <Space orientation="vertical" size={4}>
                    <span>
                      {isDomainError(error) ? error.message : "请稍后重试。"}
                    </span>
                    {isDomainError(error) && error.fieldErrors.length > 0 ? (
                      <ul className={styles.fieldErrorList}>
                        {error.fieldErrors.map((fieldError) => (
                          <li key={`${fieldError.path}:${fieldError.code}`}>
                            {fieldError.message}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {isDomainError(error) && error.requestId ? (
                      <Typography.Text code translate="no">
                        请求 ID：{error.requestId}
                      </Typography.Text>
                    ) : null}
                    {isDomainError(error) && error.problemCode ? (
                      <Typography.Text code translate="no">
                        问题代码：{error.problemCode}
                      </Typography.Text>
                    ) : null}
                    {isDomainError(error) ? (
                      <span>
                        {error.retryable
                          ? "服务端允许重试。"
                          : "请先核对字段、作用域或资源版本。"}
                      </span>
                    ) : null}
                    {conflict && onReload ? (
                      <Button
                        icon={<RefreshCw aria-hidden="true" size={15} />}
                        size="small"
                        onClick={onReload}
                      >
                        重新加载最新任务
                      </Button>
                    ) : null}
                  </Space>
                }
                type="error"
              />
            ) : null}

            <footer className={styles.drawerFooter}>
              <Button disabled={pending} onClick={requestClose}>
                取消
              </Button>
              <Button htmlType="submit" loading={pending} type="primary">
                {pending
                  ? mode === "create"
                    ? "正在创建…"
                    : "正在保存…"
                  : mode === "create"
                    ? "创建任务"
                    : "保存修改"}
              </Button>
            </footer>
          </Form>
        )}
      </div>

      <Modal
        cancelText="返回编辑"
        okButtonProps={{ danger: true }}
        okText="放弃更改"
        open={discardOpen}
        title="放弃未保存的更改？"
        onCancel={() => setDiscardOpen(false)}
        onOk={() => {
          setDiscardOpen(false);
          setDirty(false);
          onClose();
        }}
      >
        <p>当前表单内容尚未保存，关闭后需要重新填写。</p>
      </Modal>
    </div>
  );
}
