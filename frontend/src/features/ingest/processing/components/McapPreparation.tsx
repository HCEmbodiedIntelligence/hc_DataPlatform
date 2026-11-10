import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Collapse, Modal, Select, Space, Tag } from "antd";
import { ChevronDown, FileText } from "lucide-react";
import styles from "./LeRobotProcessingRecords.module.css";
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
type PlanVersion = {
  revision: number;
  scheme: { scheme_id: string; version: number };
  output: unknown;
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
    [picked, setPicked] = useState<string>(),
    [showChannels, setShowChannels] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsId = useId();
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
    enabled: detailsOpen && showChannels && !file.channel_inventory,
    queryFn: () =>
      request({
        method: "GET",
        path: `${root}/mcap-channels`,
        scope,
        query: { path: file.path },
      }),
  });
  const prepared = file.status === "READY";
  const pendingIntake = file.processing_id.startsWith("receipt:");
  const canReuseOriginal = !prepared && !file.config_reference?.error &&
    (!!file.config_reference?.effective_adapter || !!file.configuration.state_topic);
  // Preparing a second recording requires an explicitly newer task plan.
  const available = (versions.data?.items ?? []).filter(
    (plan) => !prepared || plan.revision > (file.config_reference?.plan_revision ?? 0),
  );
  const showPlanHelp = prepared && versions.isSuccess && available.length === 0;
  const schemeId = file.config_reference?.scheme?.scheme_id ?? file.config_reference?.scheme_id;
  const latestPlan = available[0];
  const selection = picked ?? (canReuseOriginal ? "original" : latestPlan && String(latestPlan.revision));
  const chosen = available.find(
    (v) => String(v.revision) === selection,
  );
  const retry = useMutation({
    mutationFn: () =>
      request({
        method: "POST",
        path: chosen ? `${root}:reprocess-mcap` : pendingIntake ? `${root}:process-mcap` : `${root}:retry`,
        scope,
        body: chosen
          ? { request_id: requestId, plan_revision: chosen.revision }
          : pendingIntake ? { robot_id: file.robot_id, collection_task_id: file.collection_task_id }
          : undefined,
      }),
    onSuccess: () => {
      setRequestId(crypto.randomUUID());
      setOpened(false);
      onRetry();
    },
  });
  const problem = isDomainError(retry.error) ? retry.error.problemCode : undefined;
  const mappingHelp = problem === "CAPTURE_MAPPING_MISMATCH" || problem === "CAPTURE_ASSOCIATION_REQUIRED"
      ? "请先为这台机器人关联所选版本的采集方案"
      : undefined;
  return (
    <div className={styles.file}>
      <div className={styles.fileRow}>
        <FileText size={16} aria-hidden className={styles.fileIcon} />
        <strong className={styles.fileName} title={file.path}>
          {file.path.split("/").pop() || file.path}
        </strong>
        <Tag
          color={
            file.status === "READY"
              ? "success"
              : file.status === "FAILED"
                ? "error"
                : file.status === "RUNNING"
                  ? "processing"
                  : undefined
          }
        >
          {(
            {
              PENDING: "等待处理",
              RUNNING: "准备中",
              READY: "已就绪",
              FAILED: "处理失败",
            } as Record<string, string>
          )[file.status] ?? file.status}
        </Tag>
        <div className={styles.fileActions}>
          {file.recording_id && (
            <Link
              to={`/recordings/${encodeURIComponent(file.recording_id)}/slice`}
            >
              预览与切片
            </Link>
          )}
          {!linked && canManage && (prepared || file.status === "FAILED") && (
            <Button size="small" onClick={() => {
              setPicked(undefined);
              retry.reset();
              setOpened(true);
            }}>
              {prepared ? "按新方案重处理" : "重试处理"}
            </Button>
          )}
          <Button
            size="small"
            type="text"
            aria-label={`${file.path} 详情`}
            aria-expanded={detailsOpen}
            aria-controls={detailsId}
            onClick={() => setDetailsOpen(!detailsOpen)}
          >
            详情{" "}
            <ChevronDown
              size={14}
              aria-hidden
              className={detailsOpen ? styles.chevronOpen : ""}
            />
          </Button>
        </div>
      </div>
      <div id={detailsId} hidden={!detailsOpen} className={styles.fileDetails}>
        {detailsOpen && (
          <>
            <div className={styles.path}>{file.path}</div>
            <p>
              本次方案：
              {file.config_reference?.scheme?.scheme_id ??
                file.config_reference?.name ??
                "历史快照"}
              {` · 任务方案 v${file.config_reference?.plan_revision ?? "未记录"}`}
              {` · ${file.config_reference?.effective_adapter?.parser_version ?? "历史解析器"}`}
              {file.config_reference?.reprocessing?.format_version && (
                <>
                  <br />
                  格式版本：{file.config_reference.reprocessing.format_version}
                </>
              )}
            </p>
            {file.error && <Alert type="error" title={file.error === "MCAP_RECORDING_ASSET_CONFLICT"
              ? "录制登记时遇到文件路径冲突，质检尚未开始。可沿用上次配置重试处理。"
              : file.error} />}
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
            </Space>
            {showChannels && (
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  maxHeight: 320,
                  overflow: "auto",
                }}
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
                  label: "配置快照",
                  children: (
                    <pre style={{ whiteSpace: "pre-wrap" }}>
                      {JSON.stringify(file.configuration, null, 2)}
                    </pre>
                  ),
                },
              ]}
            />
          </>
        )}
      </div>
      <Modal
        title="选择任务方案版本重处理"
        open={opened}
        onCancel={() => setOpened(false)}
        onOk={() => retry.mutate()}
        confirmLoading={retry.isPending}
        okText={prepared ? "确认按新方案重处理" : "确认重试处理"}
        okButtonProps={{
          disabled: (selection !== "original" && versions.isFetching) || (!chosen && !(selection === "original" && canReuseOriginal)),
        }}
      >
        <p>
          {chosen || prepared
            ? "确认后，系统会用已上传的原始 MCAP 自动核对所选方案的设备映射，通过后生成新录制。"
            : canReuseOriginal && file.config_reference?.plan_revision
              ? `沿用上次实际配置会继续使用任务方案 v${file.config_reference.plan_revision}，保留已完成的文件处理结果。`
              : "选择这次处理使用的任务方案。"}
        </p>
        <Select
          aria-label="重试配置版本"
          disabled={showPlanHelp}
          notFoundContent={prepared ? "暂无更新的任务方案" : undefined}
          style={{ width: "100%" }}
          placeholder={versions.isFetching ? "正在读取任务方案…" : prepared ? "请先将任务更新到新的采集方案版本" : "暂无任务方案"}
          value={selection}
          onChange={(value) => {
            setPicked(value);
            setRequestId(crypto.randomUUID());
            retry.reset();
          }}
          options={[
            ...(prepared ? [] : [{ value: "original", label: "沿用上次实际配置", disabled: !canReuseOriginal }]),
            ...available.map((v) => ({
              value: String(v.revision),
              label: `任务方案 v${v.revision} · 采集方案 v${v.scheme.version}`,
            })),
          ]}
        />
        {showPlanHelp && (
          <Alert
            type="info"
            title={file.config_reference?.plan_revision
              ? `暂无比本次录制使用的任务方案 v${file.config_reference.plan_revision} 更新的版本`
              : "此任务尚无可用于重处理的方案版本"}
            description={
              <>
                <p>先在采集方案中启用所需规则、验证并发布新版本，再为机器人关联该版本，在采集任务中选择它并保存。回到这里即可选择新版本。</p>
                <Space wrap>
                  <Link to={schemeId ? `/settings/capture-schemes/${encodeURIComponent(schemeId)}?edit=1` : "/settings/capture-schemes"}>编辑采集方案</Link>
                  <RobotProcessingLink robotId={file.robot_id} scheme={schemeId} sample={importId} file={file.path} />
                  <Link to={`/collection-tasks?taskId=${encodeURIComponent(file.collection_task_id)}`}>打开采集任务</Link>
                </Space>
              </>
            }
          />
        )}
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
  const [showAll, setShowAll] = useState(false);
  const allFiles = query.data?.files ?? [];
  const files = (
    <div className={styles.files}>
      {(showAll ? allFiles : allFiles.slice(0, 5)).map((file) => (
        <McapFile
          key={file.processing_id}
          scope={scope}
          importId={importId}
          file={file}
          linked={!!query.data?.same_recording}
          onRetry={() => void query.refetch()}
        />
      ))}
      {allFiles.length > 5 && (
        <Button
          type="text"
          size="small"
          className={styles.moreFiles}
          aria-expanded={showAll}
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? "收起文件" : `查看全部 ${allFiles.length} 个文件`}
        </Button>
      )}
    </div>
  );
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
