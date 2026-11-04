import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Collapse, Modal, Select, Space } from "antd";
import { Link } from "react-router-dom";
import type { IngestScope } from "../../../../entities/data-source";
import { request } from "../../../../shared/api/http-client";
import { isDomainError } from "../../../../shared/api/domain-error";
import { useCapabilities } from "../../../../shared/auth/use-capabilities";
import { RobotProcessingLink } from "../../../capture-schemes/RobotProcessingLink";
import { nativeRoot } from "../lerobot-processing";
export function McapRobotProfile({
  robotId,
}: {
  scope: IngestScope;
  robotId: string;
}) {
  return robotId ? <RobotProcessingLink robotId={robotId} /> : null;
}
type Version = {
  scheme_id: string;
  version: number;
  content_sha256: string;
  name: string;
  configuration: Record<string, unknown>;
  error?: string;
  plan_revision?: number;
  scheme?: { scheme_id: string; version: number };
  effective_adapter?: { parser_version: string };
  reprocessing?: { format_version: string };
};
type PlanVersion = { revision: number; scheme: { scheme_id: string; version: number }; output: unknown };
type File = {
  processing_id: string;
  path: string;
  status: string;
  error: string | null;
  recording_id: string | null;
  robot_id: string;
  collection_task_id: string;
  configuration: Record<string, unknown>;
  config_reference: Version | null;
  channel_inventory: unknown;
};
type Progress = {
  files: File[];
  history: File[];
  same_recording: { episode_id: string; evidence: unknown } | null;
};
function McapFile({
  scope,
  importId,
  file,
  onRetry,
  linked = false,
}: {
  scope: IngestScope;
  importId: string;
  file: File;
  onRetry: () => void;
  linked?: boolean;
}) {
  const canManage = useCapabilities().has("upload.manage");
  const [opened, setOpened] = useState(false),
    [picked, setPicked] = useState<string>(),
    [showChannels, setShowChannels] = useState(false);
  const root = `${nativeRoot(scope)}/${encodeURIComponent(importId)}`;
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const versions = useQuery({
    queryKey: ["mcap-retry-versions", scope, importId, file.robot_id],
    enabled: opened,
    staleTime: 0,
    queryFn: () =>
      request<{ items: PlanVersion[] }>({
        method: "GET",
        path: `${root}/mcap-plan-versions`,
        scope,
      }),
  });
  const channels = useQuery({
    queryKey: ["mcap-channels", scope, importId, file.path],
    enabled: showChannels && !file.channel_inventory,
    queryFn: () =>
      request({
        method: "GET",
        path: `${root}/mcap-channels`,
        scope,
        query: { path: file.path },
      }),
  });
  const canReuseOriginal = !file.config_reference?.error && !!file.configuration.state_topic;
  // The server returns plan revisions newest first.
  const latestPlan = versions.data?.items[0];
  const selection = picked ?? (canReuseOriginal ? "original" : latestPlan && String(latestPlan.revision));
  const chosen = versions.data?.items.find(
    (v) => String(v.revision) === selection,
  );
  const retry = useMutation({
    mutationFn: () =>
      request({
        method: "POST",
        path: chosen ? `${root}:reprocess-mcap` : `${root}:process-mcap`,
        scope,
        body: chosen ? { request_id: requestId, plan_revision: chosen.revision } : {
          robot_id: file.robot_id,
          collection_task_id: file.collection_task_id,
        },
      }),
    onSuccess: () => {
      setRequestId(crypto.randomUUID());
      setOpened(false);
      onRetry();
    },
  });
  const problem = isDomainError(retry.error) ? retry.error.problemCode : undefined;
  const mappingHelp = problem === "CAPTURE_MAPPING_UNVERIFIED"
    ? "请先用这台机器人的 MCAP 样本验证所选采集方案"
    : problem === "CAPTURE_MAPPING_MISMATCH" || problem === "CAPTURE_ASSOCIATION_REQUIRED"
      ? "请先为这台机器人关联所选版本的采集方案"
      : undefined;
  return (
    <div>
      <strong>{file.path}</strong> ·{" "}
      {(
        {
          PENDING: "等待处理",
          RUNNING: "准备录制",
          READY: "可预览与切片",
          FAILED: "处理失败",
        } as Record<string, string>
      )[file.status] ?? file.status}
      <p>
        本次方案：{file.config_reference?.scheme?.scheme_id ?? file.config_reference?.name ?? "历史快照"}
        {` · 任务方案 v${file.config_reference?.plan_revision ?? "未记录"}`}
        {` · ${file.config_reference?.effective_adapter?.parser_version ?? "历史解析器"}`}
        {file.config_reference?.reprocessing?.format_version && <><br />格式版本：{file.config_reference.reprocessing.format_version}</>}
      </p>
      {file.error && <Alert type="error" title={file.error} />}
      <Space wrap>
        <RobotProcessingLink
          robotId={file.robot_id}
          scheme={file.config_reference?.scheme_id}
          sample={importId}
          file={file.path}
        />
        <Button onClick={() => setShowChannels(!showChannels)}>
          查看原始通道
        </Button>
        <Button
          disabled={linked || !canManage || file.status !== "FAILED"}
          onClick={() => { setPicked(undefined); retry.reset(); setOpened(true); }}
        >
          重试处理
        </Button>
        {file.recording_id && (
          <Link
            to={`/recordings/${encodeURIComponent(file.recording_id)}/slice`}
          >
            预览与切片
          </Link>
        )}
      </Space>
      {showChannels && (
        <pre
          style={{ whiteSpace: "pre-wrap", maxHeight: 320, overflow: "auto" }}
        >
          {JSON.stringify(
            file.channel_inventory ??
              channels.data ??
              channels.error?.message ??
              "正在读取原文件…",
            null,
            2,
          )}
        </pre>
      )}
      <Collapse
        items={[
          {
            key: "config",
            label: "查看本批次实际配置快照",
            children: (
              <pre style={{ whiteSpace: "pre-wrap" }}>
                {JSON.stringify(file.configuration, null, 2)}
              </pre>
            ),
          },
        ]}
      />
      <Modal
        title="选择任务方案版本重处理"
        open={opened}
        onCancel={() => setOpened(false)}
        onOk={() => retry.mutate()}
        confirmLoading={retry.isPending}
        okText="确认重试处理"
        okButtonProps={{
          disabled: (selection !== "original" && versions.isFetching) || (!chosen && !(selection === "original" && canReuseOriginal)),
        }}
      >
        <p>
          选择这次处理使用的任务方案。
        </p>
        <Select
          aria-label="重试配置版本"
          style={{ width: "100%" }}
          placeholder={versions.isFetching ? "正在读取任务方案…" : "暂无任务方案"}
          value={selection}
          onChange={(value) => {
            setPicked(value);
            setRequestId(crypto.randomUUID());
            retry.reset();
          }}
          options={[
            { value: "original", label: "沿用上次实际配置", disabled: !canReuseOriginal },
            ...(versions.data?.items ?? []).map((v) => ({
              value: String(v.revision),
              label: `任务方案 v${v.revision} · 采集方案 v${v.scheme.version}`,
            })),
          ]}
        />
        {mappingHelp ? (
          <Alert
            type="warning"
            title={mappingHelp}
            action={<RobotProcessingLink robotId={file.robot_id} scheme={chosen?.scheme.scheme_id}
              sample={importId} file={file.path} />}
          />
        ) : (retry.error || versions.error) ? (
          <Alert type="error" title={(retry.error || versions.error)?.message} />
        ) : null}
      </Modal>
    </div>
  );
}
export function mcapPreparationQuery(scope: IngestScope, importId: string) {
  return {
    queryKey: ["mcap-preparation", scope, importId],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      request<Progress>({
        method: "GET",
        scope,
        signal,
        path: `${nativeRoot(scope)}/${encodeURIComponent(importId)}/mcap-processing`,
      }),
    refetchInterval: 3000,
    staleTime: 2000,
  };
}
export function McapPreparationProgress({
  scope,
  importId,
}: {
  scope: IngestScope;
  importId: string;
}) {
  const query = useQuery(mcapPreparationQuery(scope, importId));
  const files = query.data?.files.map((file) => (
    <McapFile
      key={file.processing_id}
      scope={scope}
      importId={importId}
      file={file}
      linked={!!query.data?.same_recording}
      onRetry={() => void query.refetch()}
    />
  ));
  const first = query.data?.files[0];
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      {query.data?.same_recording ? (
        <Alert
          type="success"
          title="同次录制已有成功结果"
          description={`已关联 Episode ${query.data.same_recording.episode_id}，保留原始文件，禁止重复入库。`}
        />
      ) : null}
      {query.data?.same_recording ? (
        <>
          {first && (
            <RobotProcessingLink
              robotId={first.robot_id}
              sample={importId}
              file={first.path}
            />
          )}
          <Collapse
            items={[
              {
                key: "linked-history",
                label: "查看此前错误与当时配置",
                children: files,
              },
            ]}
          />
        </>
      ) : (
        files
      )}
      {!!query.data?.history.length && (
        <Collapse
          items={[
            {
              key: "history",
              label: "处理记录与配置版本",
              children: (
                <pre style={{ whiteSpace: "pre-wrap" }}>
                  {JSON.stringify(query.data.history, null, 2)}
                </pre>
              ),
            },
          ]}
        />
      )}
      {query.error && (
        <Alert
          type="error"
          title="MCAP 进度读取失败"
          description={query.error.message}
        />
      )}
    </Space>
  );
}
