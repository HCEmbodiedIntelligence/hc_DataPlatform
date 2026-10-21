import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Modal, Select, Table } from "antd";
import {
  useRobotResolutionPlan,
  useResolveRobotAssociations,
  type ResolutionIntent,
  type ResolutionTask,
} from "./api/resolution";
import styles from "./RobotDeletionDialog.module.css";

type Confirmation = {
  intent: ResolutionIntent;
  title: string;
  description: string;
  destructive: boolean;
};

export function RobotResolutionActions({
  organizationId,
  robotId,
  canManage,
  disabled,
  onResolved,
  onBusyChange,
}: {
  organizationId: string;
  robotId: string;
  canManage: boolean;
  disabled: boolean;
  onResolved: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const plan = useRobotResolutionPlan(organizationId, robotId);
  const mutation = useResolveRobotAssociations(organizationId, robotId);
  const [target, setTarget] = useState<string>();
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [acknowledged, setAcknowledged] = useState(false);
  const [message, setMessage] = useState<string>();
  const [failure, setFailure] = useState<string>();
  const data = plan.data;
  const busy = mutation.isPending;
  const locked =
    disabled || busy || !canManage || plan.isFetching || plan.isError;
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);

  function confirm(
    value: Omit<Confirmation, "intent"> & {
      intent: Omit<ResolutionIntent, "revision" | "key">;
      key?: string;
    },
  ) {
    if (!data || locked) return;
    setFailure(undefined);
    setAcknowledged(false);
    setConfirmation({
      ...value,
      intent: {
        ...value.intent,
        revision: data.revision,
        key: value.key ?? crypto.randomUUID(),
      },
    });
  }
  function deleteTask(task: ResolutionTask) {
    confirm({
      title: `${task.retry_key ? "继续清理" : "删除任务及数据"} · ${task.task_code} / ${task.name}`,
      description: `会永久删除该任务及其专属数据集内的原始文件、视频、切片、质检结果和标注。当前共 ${task.raw_count} 条原始数据${task.other_robot_raw_count > 0 ? `，其中 ${task.other_robot_raw_count} 条来自其他机器人，也将一起删除` : ""}。`,
      destructive: true,
      intent: {
        action: "DELETE_TASK",
        project_id: task.project_id,
        task_id: task.task_id,
      },
      key: task.retry_key ?? undefined,
    });
  }
  async function execute() {
    if (!confirmation || busy || (confirmation.destructive && !acknowledged))
      return;
    try {
      const result = await mutation.mutateAsync(confirmation.intent);
      setMessage(result.message);
      setConfirmation(undefined);
      setFailure(undefined);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "处理失败，请重试。");
      setConfirmation(undefined);
    } finally {
      onResolved();
      void plan.refetch();
    }
  }
  return (
    <div className={styles.actionList}>
      {message && <Alert type="success" showIcon title={message} />}
      {failure && (
        <Alert type="error" showIcon title="处理未完成" description={failure} />
      )}
      {plan.isPending && <Alert type="info" title="正在准备处理操作…" />}
      {plan.isError && (
        <Alert
          type="error"
          showIcon
          title="处理操作加载失败"
          description={plan.error.message}
          action={<Button onClick={() => void plan.refetch()}>重试</Button>}
        />
      )}
      {data && (
        <>
          <section className={styles.actionCard}>
            <strong>转移到其他机器人实例</strong>
            <p>
              在目标实例创建新的数据与配置关联，成功后解除旧关联。文件和历史采集来源保留，原机器人的上传凭证将停用。
            </p>
            <div className={styles.transferRow}>
              <Select
                aria-label="接收关联的机器人实例"
                placeholder="选择接收关联的机器人实例"
                showSearch={{ optionFilterProp: "label" }}
                value={target}
                disabled={locked || !data.can_transfer}
                onChange={setTarget}
                options={data.targets.map((r) => ({
                  value: r.id,
                  label: `${r.name} · ${r.serial_no}`,
                }))}
                notFoundContent="暂无其他机器人实例"
              />
              <Button
                type="primary"
                disabled={locked || !data.can_transfer || !target}
                onClick={() =>
                  confirm({
                    title: "创建新关联并转移",
                    description: `接收实例：${data.targets.find((r) => r.id === target)?.name}。系统会创建独立关联和配置副本，校验成功后解除当前关联；历史文件、任务归属和处理结果保持原样。`,
                    destructive: false,
                    intent: { action: "TRANSFER", target_robot_id: target },
                  })
                }
              >
                创建新关联并转移
              </Button>
            </div>
            {data.transfer_reason && (
              <span className={styles.note}>{data.transfer_reason}</span>
            )}
            {!data.targets.length && (
              <span className={styles.note}>
                请先在机器人接入页创建接收实例。
              </span>
            )}
          </section>
          {data.tasks.length > 0 && (
            <section className={styles.actionCard}>
              <strong>删除不再需要的任务及数据</strong>
              <Table<ResolutionTask>
                size="small"
                rowKey={(t) => `${t.project_id}:${t.task_id}`}
                dataSource={data.tasks}
                pagination={
                  data.tasks.length > 3
                    ? { pageSize: 3, showSizeChanger: false }
                    : false
                }
                columns={[
                  {
                    title: "关联任务",
                    render: (_, task) => (
                      <div className={styles.record}>
                        <strong>
                          {task.task_code} · {task.name}
                        </strong>
                        <small>
                          项目 {task.project_id} · 区域 {task.region_code}
                        </small>
                        <span>
                          {task.raw_count} 条原始数据
                          {task.other_robot_raw_count > 0
                            ? `（含其他机器人 ${task.other_robot_raw_count} 条）`
                            : ""}
                        </span>
                        {task.reason && <small>{task.reason}</small>}
                      </div>
                    ),
                  },
                  {
                    title: "操作",
                    width: 170,
                    render: (_, task) => (
                      <Button
                        danger
                        disabled={locked || !task.can_delete}
                        onClick={() => deleteTask(task)}
                      >
                        {task.retry_key ? "重试数据清理" : "删除任务及数据"}
                      </Button>
                    ),
                  },
                ]}
              />
            </section>
          )}
          {data.config_count > 0 && (
            <section className={styles.actionCard}>
              <div className={styles.transferRow}>
                <div>
                  <strong>清理配置关联 · {data.config_count} 个版本</strong>
                  <p>数据清空后解除实例配置关联，保留历史版本用于追溯。</p>
                </div>
                <Button
                  disabled={locked || !data.can_clean_config}
                  onClick={() =>
                    confirm({
                      title: "清理实例配置关联",
                      description:
                        "解除当前实例的配置关联。完成后自动重新检查是否可以删除机器人。",
                      destructive: false,
                      intent: { action: "CLEAN_CONFIG" },
                    })
                  }
                >
                  清理配置关联
                </Button>
              </div>
              {data.cleanup_reason && (
                <span className={styles.note}>{data.cleanup_reason}</span>
              )}
            </section>
          )}
        </>
      )}
      <Modal
        open={!!confirmation}
        zIndex={1200}
        title={confirmation?.title}
        okText={confirmation?.destructive ? "确认永久删除" : "确认执行"}
        cancelText="取消"
        confirmLoading={busy}
        okButtonProps={{
          danger: confirmation?.destructive,
          disabled: !canManage || (confirmation?.destructive && !acknowledged),
        }}
        cancelButtonProps={{ disabled: busy }}
        closable={!busy}
        keyboard={!busy}
        onCancel={() => {
          if (!busy) setConfirmation(undefined);
        }}
        onOk={() => void execute()}
      >
        <p>{confirmation?.description}</p>
        {confirmation?.destructive && (
          <Checkbox
            checked={acknowledged}
            disabled={busy}
            onChange={(e) => setAcknowledged(e.target.checked)}
          >
            我确认删除该任务及其全部数据
          </Checkbox>
        )}
      </Modal>
    </div>
  );
}
