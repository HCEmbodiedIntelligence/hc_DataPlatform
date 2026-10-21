import { useQuery } from "@tanstack/react-query";
import { Button, Select } from "antd";
import { useMemo } from "react";
import type { IngestScope } from "../../../../entities/data-source";
import { request } from "../../../../shared/api/http-client";
import { z } from "zod";
import { listActiveCollectionTasks } from "../lerobot-targets";
import styles from "../../upload/upload.module.css";

function queryError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : fallback;
}

export function ProcessingTargetFields(props: {
  readonly scope: IngestScope;
  readonly datasetId: string;
  readonly fixedDatasetId?: string;
  readonly collectionTaskId: string;
  readonly robotId: string;
  readonly onDatasetIdChange: (value: string) => void;
  readonly onCollectionTaskIdChange: (value: string) => void;
  readonly onRobotIdChange: (value: string) => void;
}) {
  const tasks = useQuery({
    queryKey: [
      "p03-lerobot-targets",
      "collection-tasks",
      props.scope.organizationId,
      props.scope.projectId,
    ],
    queryFn: ({ signal }) => listActiveCollectionTasks(props.scope, signal),
    staleTime: 30_000,
    retry: false,
  });
  const robotSources = useQuery({
    queryKey: ["import-robots", props.scope],
    queryFn: async ({ signal }) =>
      z
        .array(
          z.object({
            robot_id: z.string(),
            display_name: z.string(),
            serial_no: z.string(),
          }),
        )
        .parse(
          await request({
            method: "GET",
            path: `/projects/${encodeURIComponent(props.scope.projectId)}/regions/${encodeURIComponent(props.scope.regionCode)}/lerobot-imports/robot-options`,
            scope: props.scope,
            scopeMode: "explicit",
            signal,
          }),
        ),
  });

  const taskOptions = useMemo(
    () =>
      (tasks.data ?? [])
        .filter(
          (task) =>
            !props.fixedDatasetId || task.dataset_id === props.fixedDatasetId,
        )
        .map((task) => ({
          value: task.collection_task_id,
          label: `${task.name} · ${task.collection_task_id}`,
        })),
    [tasks.data, props.fixedDatasetId],
  );
  const robotOptions = (robotSources.data ?? []).map((robot) => ({
    value: robot.robot_id,
    label: `${robot.display_name} · ${robot.serial_no} · ${robot.robot_id}`,
  }));

  return (
    <>
      <div>
        <dt>采集任务</dt>
        <dd>
          <Select<string>
            aria-label="采集任务 ID"
            className={styles.confirmTargetSelect}
            disabled={tasks.isError}
            loading={tasks.isPending}
            showSearch
            optionFilterProp="label"
            options={taskOptions}
            placeholder="请选择采集任务"
            value={props.collectionTaskId || undefined}
            notFoundContent="当前项目暂无可用采集任务，请先创建任务并绑定数据集"
            onChange={(value) => {
              const task = tasks.data?.find(
                (item) => item.collection_task_id === value,
              );
              if (!task) return;
              props.onCollectionTaskIdChange(value);
              props.onDatasetIdChange(task.dataset_id);
            }}
          />
          {tasks.isError ? (
            <span className={styles.confirmTargetIssue} role="alert">
              <span>
                {queryError(tasks.error, "无法读取当前项目的采集任务。")}
              </span>
              <Button
                type="link"
                size="small"
                onClick={() => void tasks.refetch()}
              >
                重新加载
              </Button>
            </span>
          ) : null}
        </dd>
      </div>
      <div>
        <dt>目标数据集</dt>
        <dd>
          <span aria-label="任务绑定的数据集">
            {props.collectionTaskId ? props.datasetId : "选择任务后自动确定"}
          </span>
          <small className={styles.confirmTargetHint}>
            数据将导入采集任务绑定的数据集
          </small>
        </dd>
      </div>
      <div>
        <dt>目标机器人 ID</dt>
        <dd>
          <Select<string>
            aria-label="目标机器人 ID"
            className={styles.confirmTargetSelect}
            disabled={robotSources.isError}
            loading={robotSources.isPending}
            showSearch
            optionFilterProp="label"
            options={robotOptions}
            placeholder="请选择目标机器人"
            value={props.robotId || undefined}
            notFoundContent={
              robotSources.isPending
                ? "正在加载可用机器人…"
                : robotSources.isError
                  ? "可用机器人加载失败"
                  : "当前项目暂无可用机器人"
            }
            onChange={props.onRobotIdChange}
          />
          {robotSources.isError ? (
            <span className={styles.confirmTargetIssue} role="alert">
              <span>
                {queryError(
                  robotSources.error,
                  "可用机器人列表加载失败，请重试。",
                )}
              </span>
              <Button
                type="link"
                size="small"
                onClick={() => void robotSources.refetch()}
              >
                重新加载
              </Button>
            </span>
          ) : null}
        </dd>
      </div>
      <div>
        <dt>Raw 写入方式</dt>
        <dd>按原目录逐对象上传；不转 MCAP，不打 ZIP/TAR</dd>
      </div>
    </>
  );
}
