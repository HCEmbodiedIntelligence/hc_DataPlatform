import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Descriptions, Progress, Space, Typography } from "antd";
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { dataUploadRoutes } from "../../app/shell/navigation-routes";
import type { IngestScope } from "../../entities/data-source";
import type { AutoQualityProblem } from "../../features/cleaning/api";
import { request } from "../../shared/api/http-client";
import {
  qualityProblemDescription,
  qualityProblemSummary,
} from "../../shared/lib/quality-presentation";
import {
  nativeRoot,
  type NativeProgress,
} from "../p03-upload-jobs/lerobot-processing";
import { getFormalRolloutQuality } from "../p04-upload-detail/formal-detail-client";

const runningStatuses = new Set(["PENDING", "RUNNING"]);
const retryableStatuses = new Set(["FAILED", "PARTIALLY_FAILED", "CANCELLED"]);
const statusLabels: Record<string, string> = {
  PENDING: "已排队，等待重新处理",
  RUNNING: "正在质检并继续处理",
  SUCCEEDED: "处理完成，可前往数据集查看和标注",
  PARTIALLY_FAILED: "部分条目未完成，请查看诊断结果后继续处理",
  FAILED: "处理未完成，请查看诊断结果后继续处理",
  CANCELLED: "处理已取消，可以重新处理",
};

export function RawDiagnosticActions({
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
  const scopeKey = [scope.organizationId, scope.projectId, scope.regionCode];
  const progressKey = [
    "native-processing",
    ...scopeKey,
    "detail",
    problem.sourceImportId,
  ];
  const qualityKey = ["raw-diagnostic-quality", ...scopeKey, problem.rolloutId];
  const path = `${nativeRoot(scope)}/${encodeURIComponent(problem.sourceImportId!)}`;
  const processing = useQuery({
    queryKey: progressKey,
    queryFn: ({ signal }) =>
      request<NativeProgress>({
        method: "GET",
        path: `${path}/processing`,
        scope,
        signal,
      }),
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
  });
  const retry = useMutation({
    mutationFn: () =>
      request<NativeProgress>({ method: "POST", path: `${path}:retry`, scope }),
    onSuccess: (next) => {
      client.setQueryData(progressKey, next);
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
    void client.invalidateQueries({ queryKey: ["dashboard"] });
  }, [
    client,
    progress?.updated_at,
    problem.rolloutId,
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
  ]);

  const latest = quality.data;
  const passed = latest?.status === "PASS";
  const codes = latest
    ? latest.findings.map((finding) => finding.code)
    : problem.findingCodes;
  const topics = latest
    ? [...new Set(latest.findings.map((finding) => finding.topic))]
    : problem.topics;
  const refresh = () => {
    void processing.refetch();
    void quality.refetch();
  };

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
        只按图像损坏、采样频率不足、动作数据异常、连续缺帧、时间戳重复或倒退、必需数据通道缺失这六类判断风险。
        重新处理会检查当前上传批次中未完成的条目，已完成的条目会跳过。
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
            当前上传批次共 {progress.episode_count} 条，已入库 {progress.ready}{" "}
            条，处理失败 {progress.failed} 条
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
              !retryableStatuses.has(progress.status) ||
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
        {canReadDataset && progress && progress.ready > 0 ? (
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
