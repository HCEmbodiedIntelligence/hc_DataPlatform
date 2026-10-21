import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Collapse, Modal, Select, Space } from "antd";
import { Link } from "react-router-dom";
import type { IngestScope } from "../../../../entities/data-source";
import { request } from "../../../../shared/api/http-client";
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
};
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
    [selection, setSelection] = useState("original"),
    [showChannels, setShowChannels] = useState(false);
  const root = `${nativeRoot(scope)}/${encodeURIComponent(importId)}`;
  const versions = useQuery({
    queryKey: ["mcap-retry-versions", scope, importId, file.robot_id],
    enabled: opened,
    queryFn: () =>
      request<{ items: Version[] }>({
        method: "GET",
        path: `${root}/mcap-versions`,
        scope,
        query: { robot_id: file.robot_id },
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
  const chosen = versions.data?.items.find(
    (v) => `${v.scheme_id}:${v.version}` === selection,
  );
  const retry = useMutation({
    mutationFn: () =>
      request({
        method: "POST",
        path: `${root}:process-mcap`,
        scope,
        body: {
          robot_id: file.robot_id,
          collection_task_id: file.collection_task_id,
          ...(chosen
            ? {
                processing_config: {
                  scheme_id: chosen.scheme_id,
                  version: chosen.version,
                  content_sha256: chosen.content_sha256,
                },
              }
            : {}),
        },
      }),
    onSuccess: () => {
      setOpened(false);
      onRetry();
    },
  });
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
        本次配置：{file.config_reference?.name ?? "历史快照"} ·{" "}
        {file.config_reference?.version
          ? `v${file.config_reference.version}`
          : "未版本化"}
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
          onClick={() => setOpened(true)}
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
        title="选择重试配置版本"
        open={opened}
        onCancel={() => setOpened(false)}
        onOk={() => retry.mutate()}
        confirmLoading={retry.isPending}
        okText="确认重试处理"
        okButtonProps={{
          disabled:
            !chosen &&
            (selection !== "original" ||
              !!file.config_reference?.error ||
              !file.configuration.state_topic),
        }}
      >
        <p>
          普通故障重试沿用原快照。选择其他已发布版本会建立新的处理记录，原记录保留。
        </p>
        <Select
          aria-label="重试配置版本"
          style={{ width: "100%" }}
          value={selection}
          onChange={setSelection}
          options={[
            { value: "original", label: "沿用上次实际配置" },
            ...(versions.data?.items ?? []).map((v) => ({
              value: `${v.scheme_id}:${v.version}`,
              label: `${v.name} · v${v.version}`,
            })),
          ]}
        />
        {chosen && (
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {JSON.stringify(
              { 上次: file.configuration, 本次: chosen.configuration },
              null,
              2,
            )}
          </pre>
        )}
        {(retry.error || versions.error) && (
          <Alert
            type="error"
            title={(retry.error || versions.error)?.message}
          />
        )}
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
