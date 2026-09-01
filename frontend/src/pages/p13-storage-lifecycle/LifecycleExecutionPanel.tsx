import {
  Alert,
  Button,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
} from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { ClipboardCheck, History, Play, RefreshCw, Timer } from "lucide-react";
import { useMemo, useState } from "react";
import {
  useApproveLifecycleExecution,
  useCancelLifecycleExecution,
  useCreateLifecycleDryRun,
  useCreateLifecycleSchedule,
  useDeleteLifecycleSchedule,
  useEnableLifecycleSchedule,
  useLifecycleExecutionLogs,
  useLifecycleExecutions,
  useLifecycleSchedules,
  usePauseLifecycleSchedule,
  useRetryLifecycleExecution,
  useStartLifecycleExecution,
  useUpdateLifecycleSchedule,
  type LifecycleExecution,
  type LifecyclePolicy,
  type LifecycleSchedule,
} from "../../features/lifecycle/api";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { DataTable, PageState, StatusTag } from "../../shared/ui";
import styles from "./styles.module.css";

type ExecutionAction = "approve" | "start" | "cancel" | "retry";

const terminalStatuses = new Set(["COMPLETED", "CANCELLED"]);

function toLocalDateTimeInput(value: string | Date): string {
  const date = typeof value === "string" ? new Date(value) : value;
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function toIsoInstant(value: string): string | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function progressLabel(execution: LifecycleExecution): string {
  return `${execution.processed_items}/${execution.total_items} 已处理 · ${execution.blocked_items} 阻断 · ${execution.failed_items} 失败`;
}

export function LifecycleExecutionPanel({
  policies,
  enabled,
}: Readonly<{
  policies: readonly LifecyclePolicy[];
  enabled: boolean;
}>) {
  const capabilities = useCapabilities();
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  const canExecute =
    capabilities.has("storage.lifecycle.execute") ||
    capabilities.has("storage.lifecycle.manage");
  const canApprove = capabilities.has("storage.lifecycle.approve");
  const canManage = capabilities.has("storage.lifecycle.manage");
  const executablePolicies = policies.filter(
    (policy) =>
      policy.state === "ENABLED" &&
      ["ARCHIVE", "TRANSITION_TO_COLD", "CLEAN_REBUILDABLE_CACHE"].includes(
        policy.action,
      ),
  );
  const [selectedPolicyId, setSelectedPolicyId] = useState<string | null>(null);
  const selectedPolicy =
    executablePolicies.find(
      (policy) => policy.policy_id === selectedPolicyId,
    ) ??
    executablePolicies[0] ??
    null;
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(
    null,
  );
  const [pendingExecutionAction, setPendingExecutionAction] = useState<{
    action: ExecutionAction;
    execution: LifecycleExecution;
  } | null>(null);
  const [reason, setReason] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] =
    useState<LifecycleSchedule | null>(null);
  const [intervalSeconds, setIntervalSeconds] = useState(86_400);
  const [nextRunAtLocal, setNextRunAtLocal] = useState(() =>
    toLocalDateTimeInput(new Date(Date.now() + 86_400_000)),
  );

  const executions = useLifecycleExecutions(undefined, 25, enabled);
  const logs = useLifecycleExecutionLogs(
    selectedExecutionId,
    undefined,
    50,
    enabled,
  );
  const schedules = useLifecycleSchedules(undefined, 25, enabled);
  const dryRun = useCreateLifecycleDryRun();
  const approve = useApproveLifecycleExecution();
  const start = useStartLifecycleExecution();
  const cancel = useCancelLifecycleExecution();
  const retry = useRetryLifecycleExecution();
  const createSchedule = useCreateLifecycleSchedule();
  const updateSchedule = useUpdateLifecycleSchedule();
  const enableSchedule = useEnableLifecycleSchedule();
  const pauseSchedule = usePauseLifecycleSchedule();
  const deleteSchedule = useDeleteLifecycleSchedule();
  const operationError =
    dryRun.error ??
    approve.error ??
    start.error ??
    cancel.error ??
    retry.error ??
    createSchedule.error ??
    updateSchedule.error ??
    enableSchedule.error ??
    pauseSchedule.error ??
    deleteSchedule.error;
  const executionBusy =
    approve.isPending || start.isPending || cancel.isPending || retry.isPending;

  const confirmExecutionAction = () => {
    if (!pendingExecutionAction) return;
    const { action, execution } = pendingExecutionAction;
    const intent = {
      executionId: execution.execution_id,
      planHash: execution.plan_hash,
      idempotencyKey: crypto.randomUUID(),
      reason: reason.trim(),
      justification: reason.trim(),
      ...(execution.approval_id ? { approvalId: execution.approval_id } : {}),
    };
    const close = { onSuccess: () => setPendingExecutionAction(null) };
    if (action === "approve") approve.mutate(intent, close);
    if (action === "start") start.mutate(intent, close);
    if (action === "cancel") cancel.mutate(intent, close);
    if (action === "retry") retry.mutate(intent, close);
  };

  const executionColumns = useMemo<
    readonly ColumnDef<LifecycleExecution, unknown>[]
  >(
    () => [
      {
        id: "identity",
        header: "执行 / 策略",
        size: 130,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <code>{row.original.execution_id}</code>
            <small>
              {row.original.policy_id} · v{row.original.policy_version}
            </small>
          </span>
        ),
      },
      {
        id: "status",
        header: "状态 / 进度",
        size: 142,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <StatusTag
              status={row.original.status}
              label={row.original.status}
              tone={
                row.original.status === "COMPLETED"
                  ? "success"
                  : ["FAILED", "BLOCKED"].includes(row.original.status)
                    ? "danger"
                    : "warning"
              }
            />
            <progress
              max={Math.max(1, row.original.total_items)}
              value={row.original.processed_items}
              aria-label={progressLabel(row.original)}
            />
            <small>{progressLabel(row.original)}</small>
          </span>
        ),
      },
      {
        id: "approval",
        header: "申请 / 审批",
        size: 110,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <small>申请 {row.original.requested_by}</small>
            <small>审批 {row.original.approved_by ?? "待独立审批"}</small>
          </span>
        ),
      },
      {
        id: "actions",
        header: "控制",
        size: 190,
        cell: ({ row }) => {
          const value = row.original;
          return (
            <Space size={4} wrap>
              <Button
                size="small"
                icon={<History aria-hidden="true" size={13} />}
                onClick={() => setSelectedExecutionId(value.execution_id)}
              >
                日志
              </Button>
              {value.status === "AWAITING_APPROVAL" ? (
                <Button
                  size="small"
                  icon={<ClipboardCheck aria-hidden="true" size={13} />}
                  disabled={
                    !canApprove ||
                    value.requested_by === principalId ||
                    executionBusy
                  }
                  onClick={() => {
                    setReason("");
                    setPendingExecutionAction({
                      action: "approve",
                      execution: value,
                    });
                  }}
                >
                  审批
                </Button>
              ) : null}
              {value.status === "APPROVED" && value.approval_id ? (
                <Button
                  size="small"
                  type="primary"
                  icon={<Play aria-hidden="true" size={13} />}
                  disabled={!canExecute || executionBusy}
                  onClick={() => {
                    setReason("按已审批计划开始生产执行");
                    setPendingExecutionAction({
                      action: "start",
                      execution: value,
                    });
                  }}
                >
                  启动
                </Button>
              ) : null}
              {["FAILED", "BLOCKED"].includes(value.status) &&
              value.approval_id ? (
                <Button
                  size="small"
                  disabled={!canExecute || executionBusy}
                  onClick={() => {
                    setReason("");
                    setPendingExecutionAction({
                      action: "retry",
                      execution: value,
                    });
                  }}
                >
                  续跑
                </Button>
              ) : null}
              {!terminalStatuses.has(value.status) ? (
                <Button
                  size="small"
                  danger
                  disabled={!canExecute || executionBusy}
                  onClick={() => {
                    setReason("");
                    setPendingExecutionAction({
                      action: "cancel",
                      execution: value,
                    });
                  }}
                >
                  取消
                </Button>
              ) : null}
            </Space>
          );
        },
      },
    ],
    [canApprove, canExecute, executionBusy, principalId],
  );

  const scheduleColumns = useMemo<
    readonly ColumnDef<LifecycleSchedule, unknown>[]
  >(
    () => [
      {
        id: "schedule",
        header: "计划 / 策略",
        size: 130,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <code>{row.original.schedule_id}</code>
            <small>{row.original.policy_id}</small>
          </span>
        ),
      },
      {
        id: "timing",
        header: "周期 / 下次运行",
        size: 130,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <span>
              每 {Math.round(row.original.interval_seconds / 3600)} 小时
            </span>
            <time dateTime={row.original.next_run_at}>
              {new Date(row.original.next_run_at).toLocaleString("zh-CN")}
            </time>
          </span>
        ),
      },
      {
        id: "state",
        header: "状态",
        size: 82,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.enabled ? "ENABLED" : "PAUSED"}
            label={row.original.enabled ? "已启用" : "已暂停"}
            tone={row.original.enabled ? "success" : "warning"}
          />
        ),
      },
      {
        id: "actions",
        header: "操作",
        size: 180,
        cell: ({ row }) => (
          <Space size={4} wrap>
            <Button
              size="small"
              disabled={!canManage}
              onClick={() => {
                setEditingSchedule(row.original);
                setIntervalSeconds(row.original.interval_seconds);
                setNextRunAtLocal(
                  toLocalDateTimeInput(row.original.next_run_at),
                );
                setScheduleOpen(true);
              }}
            >
              修改
            </Button>
            <Button
              size="small"
              disabled={!canManage}
              onClick={() => {
                const mutation = row.original.enabled
                  ? pauseSchedule
                  : enableSchedule;
                mutation.mutate({
                  scheduleId: row.original.schedule_id,
                  etag: row.original.etag,
                  idempotencyKey: crypto.randomUUID(),
                });
              }}
            >
              {row.original.enabled ? "暂停" : "启用"}
            </Button>
            <Popconfirm
              title="删除计划任务？"
              description="仅已暂停计划可以删除；既有执行与审计保留。"
              okText="删除"
              cancelText="取消"
              disabled={row.original.enabled || !canManage}
              onConfirm={() =>
                deleteSchedule.mutate({
                  scheduleId: row.original.schedule_id,
                  etag: row.original.etag,
                  idempotencyKey: crypto.randomUUID(),
                })
              }
            >
              <Button
                size="small"
                danger
                disabled={row.original.enabled || !canManage}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    [canManage, deleteSchedule, enableSchedule, pauseSchedule],
  );

  return (
    <>
      <section className={styles.dataSection} aria-labelledby="execution-title">
        <div className={styles.sectionHeading}>
          <div>
            <h3 id="execution-title">执行计划与进度</h3>
          </div>
          <Space>
            <Select
              size="small"
              aria-label="选择 dry-run 策略"
              value={selectedPolicy?.policy_id}
              options={executablePolicies.map((policy) => ({
                value: policy.policy_id,
                label: policy.name,
              }))}
              placeholder="选择已启用物理策略"
              onChange={setSelectedPolicyId}
            />
            <Button
              size="small"
              type="primary"
              disabled={!canExecute || !selectedPolicy}
              loading={dryRun.isPending}
              onClick={() =>
                selectedPolicy &&
                dryRun.mutate({
                  policyId: selectedPolicy.policy_id,
                  policyEtag: selectedPolicy.etag,
                  idempotencyKey: crypto.randomUUID(),
                })
              }
            >
              新建 dry-run
            </Button>
            <Button
              size="small"
              icon={<RefreshCw aria-hidden="true" size={13} />}
              onClick={() => void executions.refetch()}
            >
              刷新
            </Button>
          </Space>
        </div>
        {executions.isPending ? (
          <PageState state="loading" label="生命周期执行" />
        ) : executions.data?.items.length ? (
          <DataTable
            data={executions.data.items}
            columns={executionColumns}
            getRowId={(item) => item.execution_id}
            caption="生命周期执行计划与进度"
          />
        ) : (
          <PageState
            state="empty"
            label="生命周期执行"
            description="先为已启用的物理策略创建 dry-run。"
          />
        )}
      </section>

      <section className={styles.dataSection} aria-labelledby="schedule-title">
        <div className={styles.sectionHeading}>
          <div>
            <h3 id="schedule-title">计划任务</h3>
          </div>
          <Button
            size="small"
            icon={<Timer aria-hidden="true" size={13} />}
            disabled={!canManage || !selectedPolicy}
            onClick={() => {
              setEditingSchedule(null);
              setIntervalSeconds(86_400);
              setNextRunAtLocal(
                toLocalDateTimeInput(new Date(Date.now() + 86_400_000)),
              );
              setScheduleOpen(true);
            }}
          >
            新建计划
          </Button>
        </div>
        {schedules.isPending ? (
          <PageState state="loading" label="生命周期计划任务" />
        ) : schedules.data?.items.length ? (
          <DataTable
            data={schedules.data.items}
            columns={scheduleColumns}
            getRowId={(item) => item.schedule_id}
            caption="生命周期计划任务"
          />
        ) : (
          <PageState state="empty" label="生命周期计划任务" />
        )}
      </section>

      {operationError ? (
        <Alert
          className={styles.operationAlert}
          type="error"
          showIcon
          title="生命周期执行操作未完成"
          description={
            isDomainError(operationError)
              ? operationError.message
              : "服务端没有推进执行状态。"
          }
        />
      ) : null}

      <Modal
        open={pendingExecutionAction !== null}
        title={
          pendingExecutionAction?.action === "approve"
            ? "独立审批执行计划"
            : pendingExecutionAction?.action === "start"
              ? "启动生产物理执行"
              : pendingExecutionAction?.action === "retry"
                ? "从断点续跑"
                : "取消执行"
        }
        okText="确认"
        cancelText="返回"
        okButtonProps={{
          danger: ["start", "cancel"].includes(
            pendingExecutionAction?.action ?? "",
          ),
          loading: executionBusy,
          disabled:
            pendingExecutionAction?.action !== "start" &&
            reason.trim().length < 8,
        }}
        onOk={confirmExecutionAction}
        onCancel={() => setPendingExecutionAction(null)}
      >
        <p>
          计划哈希 <code>{pendingExecutionAction?.execution.plan_hash}</code>
        </p>
        {pendingExecutionAction?.action !== "start" ? (
          <Input.TextArea
            value={reason}
            rows={3}
            maxLength={1024}
            placeholder="填写原因或审批意见（至少 8 个字符）"
            onChange={(event) => setReason(event.target.value)}
          />
        ) : null}
      </Modal>

      <Modal
        open={selectedExecutionId !== null}
        title="执行日志"
        footer={
          <Button onClick={() => setSelectedExecutionId(null)}>关闭</Button>
        }
        onCancel={() => setSelectedExecutionId(null)}
      >
        {logs.isPending ? (
          <PageState state="loading" label="执行日志" />
        ) : logs.data?.items.length ? (
          <ol className={styles.executionLogs}>
            {logs.data.items.map((log) => (
              <li key={log.sequence} data-level={log.level}>
                <time dateTime={log.occurred_at}>
                  {new Date(log.occurred_at).toLocaleString("zh-CN")}
                </time>
                <code>{log.event}</code>
                <small>{JSON.stringify(log.details ?? {})}</small>
              </li>
            ))}
          </ol>
        ) : (
          <PageState state="empty" label="执行日志" />
        )}
      </Modal>

      <Modal
        open={scheduleOpen}
        title={editingSchedule ? "修改生命周期计划" : "新建生命周期计划"}
        okText={editingSchedule ? "保存修改" : "创建计划"}
        cancelText="取消"
        okButtonProps={{
          loading: createSchedule.isPending || updateSchedule.isPending,
          disabled: toIsoInstant(nextRunAtLocal) === null,
        }}
        onOk={() => {
          const nextRunAt = toIsoInstant(nextRunAtLocal);
          if (!nextRunAt) return;
          const close = { onSuccess: () => setScheduleOpen(false) };
          if (editingSchedule) {
            updateSchedule.mutate(
              {
                scheduleId: editingSchedule.schedule_id,
                intervalSeconds,
                nextRunAt,
                etag: editingSchedule.etag,
                idempotencyKey: crypto.randomUUID(),
              },
              close,
            );
            return;
          }
          if (selectedPolicy) {
            createSchedule.mutate(
              {
                policyId: selectedPolicy.policy_id,
                intervalSeconds,
                firstRunAt: nextRunAt,
                idempotencyKey: crypto.randomUUID(),
              },
              close,
            );
          }
        }}
        onCancel={() => setScheduleOpen(false)}
      >
        <p>
          策略{" "}
          <code>{editingSchedule?.policy_id ?? selectedPolicy?.policy_id}</code>
        </p>
        <div className={styles.scheduleForm}>
          <label>
            <span>运行周期（小时）</span>
            <InputNumber
              className={styles.fullWidth}
              aria-label="运行周期（小时）"
              min={1}
              max={744}
              precision={0}
              value={Math.round(intervalSeconds / 3600)}
              onChange={(value) =>
                setIntervalSeconds(Number(value ?? 24) * 3600)
              }
            />
          </label>
          <label>
            <span>下次生成 dry-run</span>
            <Input
              type="datetime-local"
              aria-label="下次生成 dry-run"
              value={nextRunAtLocal}
              onChange={(event) => setNextRunAtLocal(event.target.value)}
            />
          </label>
        </div>
      </Modal>
    </>
  );
}
