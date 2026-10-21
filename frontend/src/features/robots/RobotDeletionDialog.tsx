import { useEffect, useState } from "react";
import { Alert, Button, Modal, Select, Space, Table, Tabs, Tag } from "antd";
import { Link } from "react-router-dom";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  useRobotDeletionCheck,
  type DeletionKind,
  type DeletionReference,
} from "./api/deletion";
import styles from "./RobotDeletionDialog.module.css";
import { RobotResolutionActions } from "./RobotResolutionActions";

const labels: Record<DeletionKind, string> = {
  raw: "原始数据",
  upload: "上传记录",
  configuration: "已发布采集配置",
  component: "拓扑组件",
  calibration: "标定记录",
  source: "数据源",
};
const states: Record<string, string> = {
  READY: "处理完成",
  FAILED: "失败",
  PENDING: "待处理",
  PROCESSING: "处理中",
  COMMITTED: "已上传",
  UPLOADING: "上传中",
  PAUSED: "已暂停",
  CANCELLED: "已取消",
  READY_TO_COMMIT: "待提交",
  PARTIALLY_FAILED: "部分失败",
  PUBLISHED: "已发布",
  ENABLED: "已启用",
  DISABLED: "已停用",
  ACTIVE: "使用中",
  RETIRED: "已退役",
};

export function RobotDeletionDialog({
  organizationId,
  robot,
  canManage,
  pending,
  error,
  onCancel,
  onDelete,
}: {
  organizationId: string;
  robot: { id: string; displayName: string; serialNo: string };
  canManage: boolean;
  pending: boolean;
  error: Error | null;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const [resolving, setResolving] = useState(false);
  const busy = pending || resolving;
  const [kind, setKind] = useState<DeletionKind>();
  const [offset, setOffset] = useState(0);
  const scope = useShellStore((s) => s.scope);
  const check = useRobotDeletionCheck(organizationId, robot.id, kind, offset);
  const { refetch } = check;
  useEffect(() => {
    if (error) void refetch();
  }, [error, refetch]);
  const report = check.data;
  const blocked = report && !report.can_delete;
  const link = (path: string, text: string) => (
    <Link to={path} onClick={onCancel}>
      {text}
    </Link>
  );

  function actions(row: DeletionReference) {
    if (
      row.project_id &&
      (scope?.organizationId !== organizationId ||
        scope.projectId !== row.project_id ||
        scope.regionCode !== row.region_code)
    ) {
      return (
        <span>
          请先在顶部切换至项目 {row.project_id} / 区域 {row.region_code}
          ，再打开此清单查看。
        </span>
      );
    }
    return (
      <Space orientation="vertical" size={2}>
        {row.collection_task_id &&
          row.task_name &&
          link(
            `/collection-tasks?taskId=${encodeURIComponent(row.collection_task_id)}`,
            "查看所属任务",
          )}
        {row.raw_source_id &&
          link(
            `/ingest/uploads/records?rawSource=${encodeURIComponent(row.raw_source_id)}`,
            "查看上传与处理",
          )}
        {row.dataset_id &&
          link(
            `/datasets/${encodeURIComponent(row.dataset_id)}?tab=episodes${row.collection_task_id ? `&collectionTaskId=${encodeURIComponent(row.collection_task_id)}` : ""}`,
            "查看数据集",
          )}
        {row.kind === "configuration" &&
          link(
            `/settings/robot-instances?robot=${encodeURIComponent(robot.id)}&section=processing&scheme=${encodeURIComponent(row.scheme_id ?? "")}`,
            "查看方案版本",
          )}
        {row.kind === "calibration" &&
          link(
            `/settings/calibrations?robotId=${encodeURIComponent(row.origin_robot_id ?? robot.id)}&setId=${encodeURIComponent(row.id)}&version=${row.version}`,
            "查看标定",
          )}
        {row.kind === "source" &&
          link(
            `/ingest/sources?tab=external&sourceId=${encodeURIComponent(row.id)}`,
            "查看数据源",
          )}
      </Space>
    );
  }
  return (
    <Modal
      open
      zIndex={1100}
      width={960}
      title={`删除机器人 · ${robot.displayName}`}
      cancelText="关闭"
      okText={blocked ? "有关联，暂不能删除" : "确认删除机器人"}
      confirmLoading={busy}
      closable={!busy}
      keyboard={!busy}
      cancelButtonProps={{ disabled: busy }}
      okButtonProps={{
        danger: true,
        disabled:
          busy ||
          !canManage ||
          !report?.can_delete ||
          check.isFetching ||
          check.isError,
      }}
      onCancel={() => {
        if (!busy) onCancel();
      }}
      onOk={() => {
        if (
          canManage &&
          report?.can_delete &&
          !busy &&
          !check.isFetching &&
          !check.isError
        )
          onDelete();
      }}
    >
      <div className={styles.content}>
        <div>序列号：{robot.serialNo}</div>
        {error && (
          <Alert
            type="error"
            showIcon
            title="机器人未删除"
            description={error.message}
          />
        )}
        {check.isError && (
          <Alert
            type="error"
            showIcon
            title="关联检查失败，暂不能确认删除"
            description={check.error.message}
          />
        )}
        {check.isPending && <Alert type="info" title="正在检查关联数据…" />}
        {report && (
          <>
            <Alert
              type={blocked ? "warning" : "info"}
              showIcon
              title={
                blocked
                  ? "这台机器人仍有关联记录，尚未删除"
                  : "将删除整台机器人实例"
              }
              description={
                blocked ? (
                  <>
                    <div>
                      {report.blockers
                        .map((b) => `${b.label} ${b.count} 条`)
                        .join("；")}
                      。
                    </div>
                    <div>
                      使用下方按钮建立新关联并转移，或删除不再需要的数据。
                    </div>
                    <div>
                      处理完成后自动重新检查，全部解除后可确认删除机器人。
                    </div>
                  </>
                ) : (
                  "没有阻止删除的关联记录。确认后删除此实例及未使用的草稿，停用上传凭证。已转移的数据和历史采集来源保留。"
                )
              }
            />
            {blocked && (
              <Tabs
                defaultActiveKey="resolution"
                items={[
                  {
                    key: "records",
                    label: "关联清单",
                    children: (
                      <>
                        <div className={styles.toolbar}>
                          <Select<DeletionKind | "all">
                            aria-label="关联类型"
                            value={kind ?? "all"}
                            onChange={(value) => {
                              setKind(value === "all" ? undefined : value);
                              setOffset(0);
                            }}
                            options={[
                              { value: "all", label: "全部关联" },
                              ...report.blockers.map((b) => ({
                                value: b.kind,
                                label: `${b.label}（${b.count}）`,
                              })),
                            ]}
                          />
                          <Button
                            loading={check.isFetching}
                            onClick={() => void refetch()}
                          >
                            刷新检查
                          </Button>
                        </div>
                        <p className={styles.note}>
                          原始数据与上传记录可能来自同一个包，以上是关联记录数，不是
                          Episode 数。文件列展示首个文件及记录编号。
                        </p>
                        {report.restricted_count > 0 && (
                          <Alert
                            type="info"
                            showIcon
                            title={`另有 ${report.restricted_count} 条记录不在你的数据查看权限内`}
                            description="请组织或项目管理员核对这些关联；此处不显示其文件、任务名称和编号。"
                          />
                        )}
                        <Table<DeletionReference>
                          size="small"
                          loading={check.isFetching}
                          dataSource={report.items}
                          rowKey={(row) =>
                            `${row.kind}:${row.project_id}:${row.region_code}:${row.id}`
                          }
                          scroll={{ x: 790 }}
                          pagination={{
                            current: Math.floor(offset / 3) + 1,
                            pageSize: 3,
                            total: report.total,
                            showSizeChanger: false,
                            onChange: (page) => setOffset((page - 1) * 3),
                            showTotal: (total) => `可查看 ${total} 条关联`,
                          }}
                          columns={[
                            {
                              title: "关联数据",
                              width: 300,
                              render: (_, row) => (
                                <div className={styles.record}>
                                  <Tag>{labels[row.kind]}</Tag>
                                  <strong>
                                    {row.name}
                                    {row.version !== null
                                      ? ` · v${row.version}`
                                      : ""}
                                  </strong>
                                  <small>{row.id}</small>
                                  {row.status && (
                                    <span>
                                      {states[row.status] ?? row.status}
                                    </span>
                                  )}
                                </div>
                              ),
                            },
                            {
                              title: "所属任务 / 位置",
                              width: 285,
                              render: (_, row) => (
                                <div className={styles.record}>
                                  <strong>
                                    {row.task_name
                                      ? `任务 ${row.task_code ?? ""} · ${row.task_name}`
                                      : row.collection_task_id
                                        ? "任务记录已不存在，需管理员核对"
                                        : row.project_id
                                          ? "未关联采集任务"
                                          : "机器人级记录，不属于单个任务"}
                                  </strong>
                                  {row.collection_task_id && (
                                    <small>
                                      任务 ID：{row.collection_task_id}
                                    </small>
                                  )}
                                  {row.project_id && (
                                    <small>
                                      项目：{row.project_id} / 区域：
                                      {row.region_code}
                                    </small>
                                  )}
                                </div>
                              ),
                            },
                            {
                              title: "查看与处理",
                              width: 170,
                              render: (_, row) => actions(row),
                            },
                          ]}
                        />
                      </>
                    ),
                  },
                  {
                    key: "resolution",
                    label: "处理操作",
                    children: (
                      <RobotResolutionActions
                        organizationId={organizationId}
                        robotId={robot.id}
                        canManage={canManage}
                        disabled={pending}
                        onBusyChange={setResolving}
                        onResolved={() => {
                          setKind(undefined);
                          setOffset(0);
                          void refetch();
                        }}
                      />
                    ),
                  },
                ]}
              />
            )}
          </>
        )}
        {check.isError && (
          <Button onClick={() => void refetch()}>重新检查</Button>
        )}
      </div>
    </Modal>
  );
}
