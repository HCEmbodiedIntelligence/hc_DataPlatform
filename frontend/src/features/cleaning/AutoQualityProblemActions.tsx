import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Descriptions, Progress, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { dataUploadRoutes } from "../../shared/routing/navigation-routes";
import type { IngestScope } from "../../entities/data-source";
import type { AutoQualityProblem } from "./api";
import {
  qualityProblemDescription,
  qualityProblemSummary,
} from "../../shared/lib/quality-presentation";
import { getFormalRolloutQuality } from "../ingest/detail/client";
import {
  getAutoQualityProcessing,
  retryAutoQualityProcessing,
} from "./api/auto-quality-processing";
import { DeleteAutoQualityProblem } from "./DeleteAutoQualityProblem";

const runningStatuses = new Set(["PENDING", "RUNNING"]);
const statusLabels: Record<string, string> = {
  PENDING: "已排队，等待重新处理",
  RUNNING: "正在质检并继续处理",
  SUCCEEDED: "处理完成，可前往数据集查看和标注",
  PARTIALLY_FAILED: "部分条目未完成，请查看诊断结果后继续处理",
  FAILED: "处理未完成，请查看诊断结果后继续处理",
  CANCELLED: "处理已取消，可以重新处理",
  DISCARDED: "这条问题数据已删除，不再参与处理和入库",
};

export function AutoQualityProblemActions({
  scope,
  problem,
  canManage,
  canReadDataset,
}: {
  readonly scope: IngestScope;
  readonly problem: AutoQualityProblem;
  readonly canManage: boolean;
  readonly canReadDataset: boolean;
}) {
  const client = useQueryClient();
  const [deleted, setDeleted] = useState(false);
  const scopeKey = [scope.organizationId, scope.projectId, scope.regionCode];
  const retryRequest = useRef<string | null>(null);
  const progressKey = [
    "auto-quality-processing",
    ...scopeKey,
    "detail",
    problem.rolloutId,
  ];
  const qualityKey = ["raw-diagnostic-quality", ...scopeKey, problem.rolloutId];
  const processing = useQuery({
    queryKey: progressKey,
    queryFn: ({ signal }) => getAutoQualityProcessing(scope, problem, signal),
    refetchInterval: (query) =>
      runningStatuses.has(query.state.data?.status ?? "") ? 3_000 : false,
    retry: false,
  });
  const progress = processing.data;
  const running = runningStatuses.has(progress?.status ?? "");
  const quality = useQuery({
    queryKey: qualityKey,
    queryFn: ({ signal }) =>
      getFormalRolloutQuality(scope, problem.rolloutId, signal),
    refetchInterval: running ? 3_000 : false,
    retry: false,
    enabled: !problem.recordingId,
  });
  const retry = useMutation({
    mutationFn: () => {
      retryRequest.current ??= crypto.randomUUID();
      return retryAutoQualityProcessing(
        scope,
        problem,
        progress!,
        retryRequest.current,
      );
    },
    onSuccess: (next) => {
      client.setQueryData(progressKey, next);
      retryRequest.current = null;
      void client.invalidateQueries({
        queryKey: ["native-processing", ...scopeKey],
      });
    },
    onError: () => {
      void processing.refetch();
    },
  });

  // Refresh the latest result when the batch advances or finishes. The modal keeps
  // its selected episode even when a resolved issue disappears from the list.
  useEffect(() => {
    if (!progress?.updated_at) return;
    void client.invalidateQueries({
      queryKey: [
        "raw-diagnostic-quality",
        scope.organizationId,
        scope.projectId,
        scope.regionCode,
        problem.rolloutId,
      ],
    });
    void client.invalidateQueries({
      queryKey: [
        "quality-problems",
        scope.organizationId,
        scope.projectId,
        scope.regionCode,
      ],
    });
    for (const key of [
      "dashboard",
      "capture-progress",
      "collection-task-progress",
      "continuous-recording-processing",
    ])
      void client.invalidateQueries({ queryKey: [key] });
  }, [
    client,
    progress?.updated_at,
    problem.rolloutId,
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
  ]);

  const latest = problem.recordingId ? progress?.quality : quality.data;
  const passed = latest?.status === "PASS";
  const codes = latest
    ? latest.findings.map((finding) => finding.code)
    : problem.findingCodes;
  const topics = latest
    ? [...new Set(latest.findings.map((finding) => finding.topic))]
    : problem.topics;
  const refresh = () => {
    void processing.refetch();
    if (!problem.recordingId) void quality.refetch();
  };

  if (deleted)
    return (
      <Alert
        showIcon
        type="success"
        title="这条问题数据已删除"
        description="该 Episode 已退出处理和入库，原始文件和操作记录已保留。"
      />
    );

  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Alert
        showIcon
        type={passed ? "success" : "warning"}
        title={
          passed
            ? "当前条目质检通过"
            : `${latest ? "当前质检结果" : "上次质检结果"} · ${qualityProblemSummary(codes)}`
        }
        description={
          passed
            ? "当前条目已无质检风险。若上传批次仍未完成，请继续处理；处理完成后可进入数据集。"
            : qualityProblemDescription(codes)
        }
      />
      {!passed && topics.length > 0 ? (
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="发现的问题">
            {qualityProblemSummary(codes)}
          </Descriptions.Item>
          <Descriptions.Item label="相机 / 通道">
            {topics.join("、")}
          </Descriptions.Item>
        </Descriptions>
      ) : null}
      <Typography.Text>
        下一步：重新质检并继续处理。通过后进入数据集；仍有风险时修正数据后重新上传。
      </Typography.Text>
      <Typography.Text type="secondary">
        重新处理按当前质检规则检查原始数据。缺失的数据需要修正采集后重新上传；重新处理不会直接将问题标记为通过。
        {problem.recordingId
          ? "本次操作只处理当前 Episode。"
          : "本次操作检查当前上传批次中未完成的条目，已完成的条目会跳过。"}
      </Typography.Text>
      {progress ? (
        <section aria-label="上传批次处理进度">
          <Typography.Text strong>
            {statusLabels[progress.status] ?? "请前往处理记录查看状态"}
          </Typography.Text>
          <Progress
            aria-label="已入库条目比例"
            percent={Math.round(
              (100 * progress.ready) / Math.max(1, progress.episode_count),
            )}
            status={
              running ? "active" : progress.failed > 0 ? "exception" : undefined
            }
          />
          <Typography.Text>
            {progress.targetLabel}共 {progress.episode_count} 条，已入库{" "}
            {progress.ready} 条，处理失败 {progress.failed} 条
            {running
              ? `，等待或处理中 ${Math.max(0, progress.episode_count - progress.ready - progress.failed)} 条`
              : ""}
            。
          </Typography.Text>
        </section>
      ) : null}
      {passed && progress && progress.failed > 0 ? (
        <Typography.Text type="secondary">
          当前条目质检已通过。批次中仍有处理失败的条目，请到完整处理记录查看；处理失败与数据质检风险分别统计。
        </Typography.Text>
      ) : null}
      {processing.isError || quality.isError ? (
        <Alert
          type="warning"
          showIcon
          title="最新状态读取失败，请刷新后再操作。"
        />
      ) : null}
      {retry.isError ? (
        <Alert
          type="error"
          showIcon
          title="重新处理未能提交，请刷新状态后重试。"
        />
      ) : null}
      <Space wrap>
        {canManage ? (
          <Button
            type="primary"
            loading={retry.isPending}
            disabled={
              processing.isError ||
              !progress ||
              !progress.retryable ||
              retry.isPending
            }
            onClick={() => retry.mutate()}
          >
            {running ? "正在处理，请等待" : "重新质检并继续处理"}
          </Button>
        ) : (
          <Typography.Text type="secondary">
            请有上传管理权限的成员重新处理或上传修正数据。
          </Typography.Text>
        )}
        {canManage ? (
          <Link to={dataUploadRoutes.newUpload}>上传修正数据</Link>
        ) : null}
        {canManage && progress?.status !== "DISCARDED" ? (
          <DeleteAutoQualityProblem
            scope={scope}
            problem={problem}
            onDeleted={() => setDeleted(true)}
          />
        ) : null}
        {progress?.unavailableReason && (
          <Typography.Text type="secondary">
            {progress.unavailableReason}
          </Typography.Text>
        )}
        {canReadDataset && progress?.dataset_id && progress.ready > 0 ? (
          <Link to={`/datasets/${encodeURIComponent(progress.dataset_id)}`}>
            进入数据集查看已入库数据
          </Link>
        ) : null}
        <Link to={dataUploadRoutes.records}>查看完整处理记录</Link>
        <Button
          onClick={refresh}
          loading={processing.isFetching || quality.isFetching}
        >
          刷新状态
        </Button>
      </Space>
    </Space>
  );
}
