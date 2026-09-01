import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Input,
  InputNumber,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowLeft,
  BadgeCheck,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Keyboard,
  Plus,
  RefreshCw,
  Save,
  Scissors,
  Split,
  Trash2,
} from "lucide-react";
import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useToast } from "../../app/providers/ToastProvider";
import type { DatasetId } from "../../entities/dataset";
import type { DatasetVersionId } from "../../entities/dataset-version";
import type { EpisodeId } from "../../entities/episode";
import { routes as datasetRoutes } from "../../features/datasets/routing";
import {
  authorizeRobotModelViewerAssets,
  useRobotModelAssets,
  useRobotModelJointMappings,
  useRobotModelVersion,
} from "../../features/robot-models/api";
import { useRobotBootstrap } from "../../features/robots/api";
import {
  buildJointFrameSource,
  createLazyThreeRobotSceneLoader,
  createPlaybackClock,
  DataVisualizationWorkbench,
} from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { StandardPageScaffold } from "../../shared/ui/layout/StandardPageScaffold";
import { PageState } from "../../shared/ui/state/PageState";
import { StatusTag } from "../../shared/ui/state/StatusTag";
import { WorkflowQueuePage } from "../../shared/ui/workflow/WorkflowQueuePage";
import { annotationRoutes } from "../p08-data-annotation/routes";
import {
  recordingGateway,
  type ContinuousRecording,
  type EpisodeProcessing,
  type RecordingGateway,
  type RecordingScope,
} from "./api";
import {
  clampBoundary,
  formatDuration,
  formatTimecode,
  fromServerSlices,
  nextEpisodeId,
  nsToSeconds,
  parseNanoseconds,
  secondsToNs,
  toSliceInputs,
  validateSlices,
  type EditableSlice,
} from "./model";
import { buildRecordingJointAngleStream } from "./raw-joint-angle-stream";
import {
  buildEpisodeTimelineTracks,
  buildRecordingCameraStreams,
} from "./workbench-adapter";
import styles from "./styles.module.css";

interface PageProps {
  readonly gateway?: RecordingGateway;
  readonly capabilityOverride?: "manage" | "read-only";
}

function useRecordingScope(): RecordingScope | null {
  const shellScope = useShellStore((state) => state.scope);
  return useMemo(
    () =>
      shellScope?.projectId && shellScope.regionCode
        ? {
            organizationId: shellScope.organizationId,
            projectId: shellScope.projectId,
            regionCode: shellScope.regionCode,
          }
        : null,
    [shellScope?.organizationId, shellScope?.projectId, shellScope?.regionCode],
  );
}

function pageError(error: unknown, fallback: string): string {
  return isDomainError(error) ? error.message : fallback;
}

function durationOf(recording: ContinuousRecording): number {
  try {
    return parseNanoseconds(recording.duration_ns);
  } catch {
    return 0;
  }
}

function statusTag(recording: ContinuousRecording) {
  if (recording.status === "SLICED") {
    return (
      <StatusTag status={recording.status} label="已成片" tone="success" />
    );
  }
  if (recording.current_revision > 0) {
    return <StatusTag status="DRAFT" label="待检查" tone="warning" />;
  }
  return <StatusTag status={recording.status} label="待分割" tone="info" />;
}

type RecordingQueueStage = "PENDING" | "REVIEW" | "SLICED";

function recordingQueueStage(
  recording: ContinuousRecording,
): RecordingQueueStage {
  if (recording.status === "SLICED") return "SLICED";
  return recording.current_revision > 0 ? "REVIEW" : "PENDING";
}

const processingLabels: Readonly<Record<EpisodeProcessing["status"], string>> =
  {
    PENDING_QC: "等待质检",
    QC_RUNNING: "质检中",
    QC_FAILED: "质检未通过",
    PENDING_ALIGNMENT: "等待对齐",
    ALIGNING: "对齐与媒体处理中",
    READY: "已就绪",
    FAILED: "处理失败",
  };

function processingTone(
  status: EpisodeProcessing["status"],
): "info" | "warning" | "danger" | "success" {
  if (status === "READY") return "success";
  if (status === "QC_FAILED" || status === "FAILED") return "danger";
  if (status === "PENDING_QC" || status === "PENDING_ALIGNMENT")
    return "warning";
  return "info";
}

function RecordingListPage({
  gateway,
}: {
  readonly gateway: RecordingGateway;
}) {
  const navigate = useNavigate();
  const scope = useRecordingScope();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const deferredQuery = useDeferredValue(query);
  const requestedStage = searchParams.get("stage");
  const stage: RecordingQueueStage =
    requestedStage === "REVIEW" || requestedStage === "SLICED"
      ? requestedStage
      : "PENDING";
  const list = useQuery({
    queryKey: ["continuous-recordings", scopeKey],
    queryFn: ({ signal }) => gateway.list(scope as RecordingScope, signal),
    enabled: scope !== null,
    retry: false,
  });
  const rows = useMemo(() => {
    const normalized = deferredQuery.trim().toLocaleLowerCase("zh-CN");
    return (list.data?.items ?? [])
      .filter((recording) => {
        if (recordingQueueStage(recording) !== stage) return false;
        if (!normalized) return true;
        return [
          recording.recording_id,
          recording.rollout_id,
          recording.robot_id,
          recording.collection_task_id,
          recording.data_package_id,
        ].some((value) =>
          value.toLocaleLowerCase("zh-CN").includes(normalized),
        );
      })
      .toSorted((left, right) =>
        right.capture_started_at.localeCompare(left.capture_started_at),
      );
  }, [deferredQuery, list.data?.items, stage]);

  const counts = useMemo<Record<RecordingQueueStage, number>>(() => {
    const next = { PENDING: 0, REVIEW: 0, SLICED: 0 };
    for (const recording of list.data?.items ?? []) {
      next[recordingQueueStage(recording)] += 1;
    }
    return next;
  }, [list.data?.items]);

  const updateQuery = (value: string) => {
    const next = new URLSearchParams(searchParams);
    const normalized = value.normalize("NFC").slice(0, 100);
    if (normalized) next.set("q", normalized);
    else next.delete("q");
    setSearchParams(next, { replace: true });
  };

  const stageHref = (nextStage: RecordingQueueStage) => {
    const next = new URLSearchParams(searchParams);
    if (nextStage === "PENDING") next.delete("stage");
    else next.set("stage", nextStage);
    const encoded = next.toString();
    return encoded ? `/recordings?${encoded}` : "/recordings";
  };

  let state = null;
  if (scope === null) {
    state = (
      <PageState
        state="forbidden"
        title="请先选择项目与区域"
        description="录制数据按项目和区域隔离。"
      />
    );
  } else if (list.isPending) {
    state = <PageState state="loading" layout="list" label="录制列表" />;
  } else if (list.isError) {
    state = (
      <PageState
        state="error"
        title="录制列表加载失败"
        description={pageError(list.error, "暂时无法读取连续录制。")}
        onRetry={() => void list.refetch()}
      />
    );
  }

  if (state) {
    return (
      <StandardPageScaffold header={{ title: "录制切片" }} state={state} />
    );
  }

  return (
    <WorkflowQueuePage
      title="录制切片"
      headerActions={
        <button
          disabled={list.isFetching}
          type="button"
          onClick={() => void list.refetch()}
        >
          <RefreshCw aria-hidden="true" size={16} />
          {list.isFetching ? "刷新中…" : "刷新"}
        </button>
      }
      stages={[
        {
          key: "PENDING",
          label: "待分割",
          description: "选择片段并创建 Episode",
          queueDescription: "尚未建立切片草稿的连续录制。",
          emptyDescription: "新的连续录制上传完成后会进入此队列。",
          icon: Scissors,
          count: counts.PENDING,
          href: stageHref("PENDING"),
        },
        {
          key: "REVIEW",
          label: "待检查",
          description: "检查边界并确认切片",
          queueDescription: "已有切片草稿，等待检查边界和 Episode 信息。",
          emptyDescription: "保存的人工草稿或模型建议会进入此队列。",
          icon: ClipboardCheck,
          count: counts.REVIEW,
          href: stageHref("REVIEW"),
        },
        {
          key: "SLICED",
          label: "已分割",
          description: "查看 Episode 与处理状态",
          queueDescription: "切片已锁定并进入逐条质检与对齐流程。",
          emptyDescription: "提交成片后的录制会进入此队列。",
          icon: BadgeCheck,
          count: counts.SLICED,
          href: stageHref("SLICED"),
        },
      ]}
      selectedStage={stage}
      stageNavigationLabel="录制切片状态"
      visibleCount={rows.length}
      resultLabel="条录制"
      search={{
        id: "recording-search",
        name: "recording-search",
        label: "搜索录制、Rollout、机器人或采集任务",
        placeholder: "输入关键词…",
        value: query,
        meta: `${counts[stage]} 条处于此状态 · 共 ${list.data?.total ?? 0} 条录制`,
        onChange: updateQuery,
      }}
      tableLabel="录制表"
      table={
        <table>
          <caption>{stage} 录制列表</caption>
          <thead>
            <tr>
              <th>录制 / Rollout</th>
              <th>当前状态</th>
              <th>机器人 / 采集任务</th>
              <th>采集时间</th>
              <th>时长</th>
              <th>原始视频</th>
              <th>切片版本</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((recording) => (
              <tr key={recording.recording_id}>
                <th scope="row">
                  <span
                    className={styles.recordingPrimary}
                    title={recording.recording_id}
                  >
                    {recording.recording_id}
                  </span>
                  <small title={recording.rollout_id}>
                    {recording.rollout_id}
                  </small>
                </th>
                <td>{statusTag(recording)}</td>
                <td>
                  <span className={styles.recordingPrimary}>
                    {recording.robot_id}
                  </span>
                  <small>{recording.collection_task_id}</small>
                </td>
                <td>
                  <time dateTime={recording.capture_started_at}>
                    {new Intl.DateTimeFormat("zh-CN", {
                      dateStyle: "medium",
                      timeStyle: "short",
                    }).format(new Date(recording.capture_started_at))}
                  </time>
                </td>
                <td>
                  <span className={styles.recordingDuration}>
                    <Clock3 aria-hidden="true" size={13} />
                    {formatDuration(durationOf(recording))}
                  </span>
                </td>
                <td>
                  <span className={styles.numeric}>
                    {recording.video_asset_count} 路
                  </span>
                </td>
                <td>
                  <span className={styles.numeric}>
                    r{recording.current_revision}
                  </span>
                </td>
                <td>
                  <Button
                    type="link"
                    onClick={() =>
                      navigate(
                        `/recordings/${encodeURIComponent(recording.recording_id)}/slice`,
                      )
                    }
                  >
                    {recording.status === "SLICED"
                      ? "查看"
                      : recording.current_revision
                        ? "检查切片"
                        : "开始切片"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      }
      emptyActionLabel={query ? "清除搜索" : "重新检查队列"}
      emptyActionDisabled={!query && !scope}
      onEmptyAction={() => {
        if (query) updateQuery("");
        else void list.refetch();
      }}
    />
  );
}

function RecordingSegmentationPage({
  gateway,
  recordingId,
  capabilityOverride,
}: {
  readonly gateway: RecordingGateway;
  readonly recordingId: string;
  readonly capabilityOverride?: "manage" | "read-only";
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { modal } = App.useApp();
  const { showToast } = useToast();
  const capabilities = useCapabilities();
  const scope = useRecordingScope();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const detailKey = useMemo(
    () => ["continuous-recording", scopeKey, recordingId] as const,
    [recordingId, scopeKey],
  );
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: ({ signal }) =>
      gateway.detail(scope as RecordingScope, recordingId, signal),
    enabled: scope !== null,
    retry: false,
  });
  const sources = useQuery({
    queryKey: ["continuous-recording-video-sources", scopeKey, recordingId],
    queryFn: ({ signal }) =>
      gateway.videoSources(scope as RecordingScope, recordingId, signal),
    enabled:
      scope !== null &&
      detail.data?.data.schema_version === "continuous-recording/v2",
    retry: false,
    staleTime: 5 * 60_000,
  });
  const processing = useQuery({
    queryKey: ["continuous-recording-processing", scopeKey, recordingId],
    queryFn: ({ signal }) =>
      gateway.processing(scope as RecordingScope, recordingId, signal),
    enabled: scope !== null,
    retry: false,
    refetchInterval: (query) => {
      const episodes = query.state.data?.items ?? [];
      return episodes.some(
        (episode) =>
          episode.status !== "READY" &&
          episode.status !== "QC_FAILED" &&
          episode.status !== "FAILED",
      )
        ? 3_000
        : false;
    },
  });
  const [slices, setSlices] = useState<EditableSlice[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currentNs, setCurrentNs] = useState(0);
  const [markInNs, setMarkInNs] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const initializedEtag = useRef<string | null>(null);

  const recording = detail.data?.data;
  const revision = detail.data?.current_slice_revision ?? null;
  const durationNs = recording ? durationOf(recording) : 0;
  const clock = useMemo(
    () =>
      createPlaybackClock({
        startNs: "0",
        endNs: String(Math.max(1, durationNs)),
      }),
    [durationNs],
  );
  const firstSource = sources.data?.sources[0];
  const minimumDurationNs = Math.max(
    1,
    Math.round(1_000_000_000 / (firstSource?.fps ?? 30)),
  );
  const canManage =
    capabilityOverride === "manage" ||
    (capabilityOverride === undefined &&
      (capabilities.has("annotation.edit") ||
        capabilities.has("upload.manage")));
  const readOnly =
    capabilityOverride === "read-only" ||
    !canManage ||
    recording?.status === "SLICED";
  const issues = useMemo(
    () => validateSlices(slices, durationNs, minimumDurationNs),
    [durationNs, minimumDurationNs, slices],
  );
  const processingByEpisode = useMemo(
    () =>
      new Map(
        (processing.data?.items ?? []).map((episode) => [
          episode.episode_id,
          episode,
        ]),
      ),
    [processing.data?.items],
  );
  const robotBootstrap = useRobotBootstrap(recording?.robot_id ?? null);
  const boundVersionId =
    robotBootstrap.data?.effectiveModelBinding?.robotModelVersionId ?? null;
  const robotModelVersion = useRobotModelVersion(boundVersionId);
  const robotModelAssets = useRobotModelAssets(boundVersionId);
  const robotJointMappings = useRobotModelJointMappings(boundVersionId);
  const rawJointStream = useMemo(
    () =>
      recording &&
      scope &&
      recording.schema_version === "continuous-recording/v2"
        ? buildRecordingJointAngleStream({
            gateway,
            scope,
            recordingId,
            durationNs: recording.duration_ns,
          })
        : null,
    [gateway, recording, recordingId, scope],
  );
  const rawJointFrameSource = useMemo(() => {
    const base = buildJointFrameSource(rawJointStream);
    if (!base) return;
    const directions = new Map(
      (robotJointMappings.data ?? []).map((mapping) => [
        mapping.source_joint_name,
        mapping.direction,
      ]),
    );
    return {
      async sampleAt(ns: string, signal: AbortSignal) {
        const frame = await base.sampleAt(ns, signal);
        return Object.fromEntries(
          Object.entries(frame).map(([joint, value]) => [
            joint,
            directions.get(joint) === "INVERTED" ? -value : value,
          ]),
        );
      },
    };
  }, [rawJointStream, robotJointMappings.data]);
  const urdfAsset = robotModelAssets.data?.find(
    (asset) => asset.role === "URDF",
  );
  const robotScene = useMemo(() => {
    if (
      !scope ||
      !robotModelVersion.data ||
      robotModelVersion.data.lifecycle !== "PUBLISHED" ||
      !urdfAsset ||
      !rawJointFrameSource
    )
      return;
    const modelId = robotModelVersion.data.robotModelId;
    const modelVersion = robotModelVersion.data.id;
    const jointMapping = Object.fromEntries(
      (robotJointMappings.data ?? []).map((mapping) => [
        mapping.source_joint_name,
        mapping.target_joint_name,
      ]),
    );
    const requiredJoints = (robotJointMappings.data ?? []).map(
      (mapping) => mapping.source_joint_name,
    );
    return {
      title: "机器人 URDF",
      modelRef: { modelId, modelVersion },
      jointMapping,
      jointFrameSource: rawJointFrameSource,
      runtimeLoader: createLazyThreeRobotSceneLoader(async (_props, signal) => {
        const viewerAssets = await authorizeRobotModelViewerAssets(
          scope.organizationId,
          modelVersion,
          robotModelAssets.data ?? [],
          signal,
        );
        return {
          manifest: { modelId, modelVersion, requiredJoints },
          ...viewerAssets,
        };
      }),
    };
  }, [
    robotJointMappings.data,
    robotModelAssets.data,
    robotModelVersion.data,
    rawJointFrameSource,
    scope,
    urdfAsset,
  ]);
  const robotSceneUnavailableReason = useMemo(() => {
    if (!recording) return undefined;
    if (!rawJointStream)
      return "当前录制不是可直接读取原始 SENSOR_DATA 的 v2 多对象录制。";
    if (robotBootstrap.isPending)
      return `正在解析机器人 ${recording.robot_id} 的模型绑定…`;
    if (robotBootstrap.isError)
      return `无法读取机器人 ${recording.robot_id} 的模型配置。`;
    if (!boundVersionId)
      return `机器人 ${recording.robot_id} 尚未绑定已发布 URDF 模型。`;
    if (robotModelVersion.isPending || robotModelAssets.isPending)
      return "正在加载已发布模型版本与 URDF 资产…";
    if (robotModelVersion.isError || robotModelAssets.isError)
      return "模型绑定已存在，但固定版本或 URDF 资产加载失败。";
    if (robotModelVersion.data?.lifecycle !== "PUBLISHED")
      return "当前机器人绑定的模型版本尚未发布。";
    if (!urdfAsset) return "当前已发布模型没有可用的 URDF 资产。";
    if (robotJointMappings.isPending) return "正在加载关节映射…";
    if (robotJointMappings.isError) return "URDF 已找到，但关节映射加载失败。";
    return undefined;
  }, [
    boundVersionId,
    recording,
    rawJointStream,
    robotBootstrap.isError,
    robotBootstrap.isPending,
    robotJointMappings.isError,
    robotJointMappings.isPending,
    robotModelAssets.isError,
    robotModelAssets.isPending,
    robotModelVersion.data?.lifecycle,
    robotModelVersion.isError,
    robotModelVersion.isPending,
    urdfAsset,
  ]);
  const cameraStreams = useMemo(
    () =>
      buildRecordingCameraStreams({
        sources: sources.data?.sources ?? [],
        refreshSource: async (assetId, signal) => {
          const refreshed = await gateway.videoSources(
            scope as RecordingScope,
            recordingId,
            signal,
          );
          const source = refreshed.sources.find(
            (candidate) => candidate.asset_id === assetId,
          );
          if (!source) throw new Error("刷新后未找到原始视频源");
          return source;
        },
      }),
    [gateway, recordingId, scope, sources.data?.sources],
  );
  const timelineTracks = useMemo(
    () => buildEpisodeTimelineTracks(slices),
    [slices],
  );
  const selectedSlice = slices.find((slice) => slice.episodeId === selectedId);

  useEffect(() => {
    if (!recording || initializedEtag.current === recording.etag) return;
    const initial = fromServerSlices(revision?.slices ?? []);
    setSlices(initial);
    setSelectedId(initial[0]?.episodeId ?? null);
    setDirty(false);
    initializedEtag.current = recording.etag;
  }, [recording, revision]);

  useEffect(() => {
    const unsubscribe = clock.subscribe((value) => setCurrentNs(Number(value)));
    return () => {
      unsubscribe();
      clock.dispose();
    };
  }, [clock]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    globalThis.addEventListener("beforeunload", warn);
    return () => globalThis.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const seek = useCallback(
    (valueNs: number) => {
      const next = Math.min(durationNs, Math.max(0, Math.round(valueNs)));
      clock.seek(String(next));
    },
    [clock, durationNs],
  );

  const updateSlices = useCallback((next: readonly EditableSlice[]) => {
    setSlices([...next]);
    setDirty(true);
  }, []);

  const addSlice = useCallback(() => {
    if (readOnly) return;
    if (markInNs === null) {
      setMarkInNs(currentNs);
      showToast({
        title: "已设置入点",
        message: "移动播放头后按 O 或点击添加 Episode。",
        tone: "info",
      });
      return;
    }
    const startNs = Math.min(markInNs, currentNs);
    const endNs = Math.max(markInNs, currentNs);
    const episodeId = nextEpisodeId(slices);
    const ordinal = slices.length + 1;
    const candidate: EditableSlice = {
      episodeId,
      startNs,
      endNs,
      title: `Episode ${String(ordinal).padStart(4, "0")}`,
      taskLabel: "",
      notes: "",
    };
    const next = [...slices, candidate].sort(
      (left, right) => left.startNs - right.startNs,
    );
    const candidateIssues = validateSlices(next, durationNs, minimumDurationNs);
    if (candidateIssues.length > 0) {
      showToast({
        title: "无法添加 Episode",
        message: candidateIssues[0]?.message ?? "请调整时间边界。",
        tone: "warning",
      });
      return;
    }
    updateSlices(next);
    setSelectedId(episodeId);
    setMarkInNs(null);
  }, [
    currentNs,
    durationNs,
    markInNs,
    minimumDurationNs,
    readOnly,
    showToast,
    slices,
    updateSlices,
  ]);

  const applyTimelineRange = useCallback(
    (rawStartNs: string, rawEndNs: string) => {
      if (readOnly) return;
      const startNs = Math.max(0, Number(rawStartNs));
      const endNs = Math.min(durationNs, Number(rawEndNs));
      if (
        !Number.isSafeInteger(startNs) ||
        !Number.isSafeInteger(endNs) ||
        endNs - startNs < minimumDurationNs
      )
        return;

      const selected = slices.find((slice) => slice.episodeId === selectedId);
      const episodeId = selected?.episodeId ?? nextEpisodeId(slices);
      const candidate: EditableSlice = selected
        ? { ...selected, startNs, endNs }
        : {
            episodeId,
            startNs,
            endNs,
            title: `Episode ${String(slices.length + 1).padStart(4, "0")}`,
            taskLabel: "",
            notes: "",
          };
      const next = selected
        ? slices.map((slice) =>
            slice.episodeId === selected.episodeId ? candidate : slice,
          )
        : [...slices, candidate];
      const ordered = next.toSorted(
        (left, right) => left.startNs - right.startNs,
      );
      const nextIssues = validateSlices(ordered, durationNs, minimumDurationNs);
      if (nextIssues.length > 0) {
        showToast({
          title: "切片边界无效",
          message: nextIssues[0]?.message ?? "请调整时间范围。",
          tone: "warning",
        });
        return;
      }
      updateSlices(ordered);
      setSelectedId(episodeId);
      setMarkInNs(null);
      seek(startNs);
    },
    [
      durationNs,
      minimumDurationNs,
      readOnly,
      seek,
      selectedId,
      showToast,
      slices,
      updateSlices,
    ],
  );

  const splitSelected = useCallback(() => {
    if (readOnly || !selectedId) return;
    const selected = slices.find((slice) => slice.episodeId === selectedId);
    if (
      !selected ||
      currentNs - selected.startNs < minimumDurationNs ||
      selected.endNs - currentNs < minimumDurationNs
    ) {
      showToast({
        title: "当前播放头不能拆分",
        message: "请将播放头放在所选 Episode 内，并与两端至少间隔一帧。",
        tone: "warning",
      });
      return;
    }
    const episodeId = nextEpisodeId(slices);
    const next = slices.flatMap((slice) =>
      slice.episodeId === selectedId
        ? [
            { ...slice, endNs: currentNs },
            {
              ...slice,
              episodeId,
              startNs: currentNs,
              title: slice.title ? `${slice.title}（续）` : "",
            },
          ]
        : [slice],
    );
    updateSlices(next);
    setSelectedId(episodeId);
  }, [
    currentNs,
    minimumDurationNs,
    readOnly,
    selectedId,
    showToast,
    slices,
    updateSlices,
  ]);

  const saveDraft = useMutation({
    mutationFn: () =>
      gateway.saveDraft(
        scope as RecordingScope,
        recordingId,
        { slices: toSliceInputs(slices) },
        recording?.etag ?? "",
      ),
    onSuccess: (result) => {
      initializedEtag.current = result.recording.etag;
      setSlices(fromServerSlices(result.revision.slices));
      setDirty(false);
      queryClient.setQueryData(detailKey, {
        data: result.recording,
        current_slice_revision: result.revision,
      });
      void queryClient.invalidateQueries({
        queryKey: ["continuous-recordings", scopeKey],
      });
      void queryClient.invalidateQueries({
        queryKey: ["continuous-recording-processing", scopeKey, recordingId],
      });
      showToast({
        title: "切片草稿已保存",
        message: `当前为第 ${result.revision.revision} 版。`,
        tone: "success",
      });
    },
    onError: (error) =>
      showToast({
        title: "草稿保存失败",
        message: pageError(error, "请刷新后重试。"),
        tone: "error",
      }),
  });

  const finalize = useMutation({
    mutationFn: () =>
      gateway.finalize(
        scope as RecordingScope,
        recordingId,
        revision?.revision ?? 0,
        recording?.etag ?? "",
      ),
    onSuccess: (result) => {
      initializedEtag.current = result.recording.etag;
      setDirty(false);
      queryClient.setQueryData(detailKey, {
        data: result.recording,
        current_slice_revision: result.revision,
      });
      void queryClient.invalidateQueries({
        queryKey: ["continuous-recordings", scopeKey],
      });
      void queryClient.invalidateQueries({
        queryKey: ["continuous-recording-processing", scopeKey, recordingId],
      });
      showToast({
        title: "Episode 已生成",
        message: `${result.revision.slices.length} 条 Episode 已进入质检与对齐流程。`,
        tone: "success",
      });
    },
    onError: (error) =>
      showToast({
        title: "提交成片失败",
        message: pageError(error, "请刷新后重试。"),
        tone: "error",
      }),
  });

  const handleKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (
      target.matches(
        "button, input, textarea, select, [role=slider], [contenteditable=true]",
      )
    )
      return;
    const frameNs = minimumDurationNs;
    if (event.key === " ") {
      event.preventDefault();
      if (clock.isPlaying()) clock.pause();
      else clock.play();
    } else if (event.key.toLocaleLowerCase() === "i" && !readOnly) {
      event.preventDefault();
      setMarkInNs(currentNs);
    } else if (event.key.toLocaleLowerCase() === "o" && !readOnly) {
      event.preventDefault();
      addSlice();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seek(currentNs - (event.shiftKey ? 1_000_000_000 : frameNs));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seek(currentNs + (event.shiftKey ? 1_000_000_000 : frameNs));
    }
  };

  const leaveToList = () => {
    if (!dirty) {
      navigate("/recordings");
      return;
    }
    modal.confirm({
      title: "放弃未保存的切片修改？",
      content: "离开后，本次尚未保存的 Episode 边界和属性会丢失。",
      okText: "放弃并离开",
      okButtonProps: { danger: true },
      cancelText: "继续编辑",
      onOk: () => navigate("/recordings"),
    });
  };

  if (scope === null) {
    return (
      <StandardPageScaffold
        header={{ title: "录制切片" }}
        state={<PageState state="forbidden" title="请先选择项目与区域" />}
      />
    );
  }
  if (detail.isPending) {
    return (
      <StandardPageScaffold
        header={{ title: "录制切片" }}
        state={
          <PageState state="loading" layout="workbench" label="切片工作台" />
        }
      />
    );
  }
  if (detail.isError || !recording) {
    return (
      <StandardPageScaffold
        header={{
          title: "录制切片",
          breadcrumbs: [
            { key: "recordings", label: "录制切片", to: "/recordings" },
            { key: "recording", label: recordingId },
          ],
        }}
        state={
          <PageState
            state="error"
            title="录制加载失败"
            description={pageError(detail.error, "无法读取该录制。")}
            onRetry={() => void detail.refetch()}
          />
        }
      />
    );
  }

  const finalizeAllowed =
    !readOnly &&
    !dirty &&
    slices.length > 0 &&
    issues.length === 0 &&
    revision?.status === "DRAFT" &&
    revision.authoring_mode === "HUMAN";
  const needsHumanReview = revision?.authoring_mode === "MODEL";

  return (
    <div
      className={styles.workbenchPage}
      tabIndex={0}
      onKeyDown={handleKeyboard}
      aria-label="录制切片工作台"
    >
      <DataVisualizationWorkbench
        showNavigation={false}
        adapter={{
          mode: "episode-slicing",
          id: `recording-slicing-${recording.recording_id}`,
          title: "录制切片",
          description: `${recording.recording_id} · ${formatDuration(durationNs)} · ${recording.video_asset_count} 路相机 · 机器人 ${recording.robot_id}`,
          readOnly,
          clock,
          cameraStreams,
          ...(robotScene ? { robotScene } : {}),
          ...(robotSceneUnavailableReason
            ? { robotSceneUnavailableReason }
            : {}),
          collectionItems: [],
          findings: [],
          timelineTracks,
          ...(selectedSlice
            ? {
                timelineSelection: {
                  startNs: String(selectedSlice.startNs),
                  endNs: String(selectedSlice.endNs),
                  label: selectedSlice.title || selectedSlice.episodeId,
                },
              }
            : {}),
          actions: [],
          onTimeRangeSelect: readOnly ? undefined : applyTimelineRange,
          ...(sources.isError
            ? {
                banner: {
                  label: "视频源",
                  title: "原视频暂时无法播放",
                  description: pageError(
                    sources.error,
                    "请检查录制格式，或刷新短期授权地址。",
                  ),
                  tone: "error" as const,
                },
              }
            : revision?.authoring_mode === "MODEL"
              ? {
                  banner: {
                    label: "待人工检查",
                    title: "当前是大模型切片建议",
                    description: "请逐条核对边界；人工保存后才可提交成片。",
                    tone: "warning" as const,
                  },
                }
              : undefined),
        }}
        slots={{
          workspaceToolbar: () => (
            <div className={styles.sharedWorkbenchToolbar}>
              <div className={styles.headerIdentity}>
                <Button
                  type="text"
                  icon={<ArrowLeft size={17} />}
                  onClick={leaveToList}
                  aria-label="返回录制列表"
                />
                <div>
                  <nav aria-label="面包屑">
                    <Link
                      to="/recordings"
                      onClick={(event) => {
                        if (!dirty) return;
                        event.preventDefault();
                        leaveToList();
                      }}
                    >
                      录制切片
                    </Link>
                    <span>/</span>
                    <span aria-current="page">{recording.recording_id}</span>
                  </nav>
                  <p>
                    直接读取 OSS 原视频与原始关节角；视频、时间轴与 URDF
                    使用同一录制时钟。
                  </p>
                </div>
              </div>
              <div className={styles.headerActions}>
                {dirty ? (
                  <Tag color="warning">有未保存修改</Tag>
                ) : (
                  <Tag color="default">
                    已保存 r{recording.current_revision}
                  </Tag>
                )}
                <Button
                  icon={<Save size={16} />}
                  disabled={
                    readOnly ||
                    (!dirty && !needsHumanReview) ||
                    issues.length > 0
                  }
                  loading={saveDraft.isPending}
                  onClick={() => saveDraft.mutate()}
                >
                  {needsHumanReview ? "确认并保存人工草稿" : "保存草稿"}
                </Button>
                <Tooltip
                  title={
                    !finalizeAllowed
                      ? "先保存人工草稿并修复所有边界问题"
                      : undefined
                  }
                >
                  <Button
                    type="primary"
                    icon={<CheckCircle2 size={16} />}
                    disabled={!finalizeAllowed}
                    loading={finalize.isPending}
                    onClick={() => {
                      modal.confirm({
                        title: `生成 ${slices.length} 条 Episode？`,
                        content:
                          "提交后切片将锁定，并进入逐条质检和对齐流程。原视频仍保留在 OSS 中。",
                        okText: "确认生成",
                        cancelText: "继续编辑",
                        onOk: () => finalize.mutateAsync(),
                      });
                    }}
                  >
                    提交成片
                  </Button>
                </Tooltip>
              </div>
              {recording.status === "SLICED" ? (
                <Alert
                  type="success"
                  showIcon
                  message="切片已最终提交"
                  description="Episode 已进入质检与对齐流程；当前页面只读。"
                />
              ) : !canManage ? (
                <Alert
                  type="info"
                  showIcon
                  message="当前为只读模式"
                  description="需要数据上传管理或标注编辑权限才能修改并提交切片。"
                />
              ) : null}
            </div>
          ),
          mediaHeader: () => (
            <p className={styles.sharedMediaHint}>
              {sources.isPending
                ? "正在授权原始视频…"
                : "直接读取 OSS，不生成预览副本"}
            </p>
          ),
          inspector: () => (
            <aside
              className={styles.episodeInspector}
              aria-label="Episode 列表与属性"
            >
              <div className={styles.inspectorHeading}>
                <div>
                  <Scissors size={17} aria-hidden="true" />
                  <strong>Episode</strong>
                  <Tag>{slices.length}</Tag>
                </div>
                <Button
                  size="small"
                  icon={<Split size={15} />}
                  disabled={readOnly || !selectedId}
                  onClick={splitSelected}
                >
                  播放头拆分
                </Button>
              </div>
              <div className={styles.episodeList}>
                {slices.length === 0 ? (
                  <div className={styles.emptyEpisodes}>
                    <Scissors size={28} aria-hidden="true" />
                    <strong>还没有 Episode</strong>
                    <span>在视频中定位入点和出点，然后添加第一条切片。</span>
                  </div>
                ) : (
                  slices.map((slice, index) => {
                    const selected = selectedId === slice.episodeId;
                    const issue = issues.find(
                      (item) => item.episodeId === slice.episodeId,
                    );
                    const episodeProcessing = processingByEpisode.get(
                      slice.episodeId,
                    );
                    return (
                      <article
                        className={styles.episodeCard}
                        data-selected={selected || undefined}
                        key={slice.episodeId}
                      >
                        <button
                          type="button"
                          className={styles.episodeCardHeader}
                          onClick={() => {
                            setSelectedId(slice.episodeId);
                            seek(slice.startNs);
                          }}
                          aria-expanded={selected}
                        >
                          <span>{String(index + 1).padStart(2, "0")}</span>
                          <span>
                            <strong>{slice.title || slice.episodeId}</strong>
                            <small>
                              {formatTimecode(slice.startNs)} —{" "}
                              {formatTimecode(slice.endNs)}
                            </small>
                          </span>
                          <span className={styles.episodeHeaderState}>
                            {episodeProcessing ? (
                              <StatusTag
                                status={episodeProcessing.status}
                                label={
                                  processingLabels[episodeProcessing.status]
                                }
                                tone={processingTone(episodeProcessing.status)}
                              />
                            ) : processing.isPending &&
                              recording.status === "SLICED" ? (
                              <Tag>读取状态</Tag>
                            ) : issue ? (
                              <Tag color="error">边界错误</Tag>
                            ) : null}
                          </span>
                        </button>
                        {selected ? (
                          <div className={styles.episodeFields}>
                            {episodeProcessing ? (
                              <div className={styles.processingPanel}>
                                <div className={styles.processingHeading}>
                                  <span>
                                    <strong>处理阶段</strong>
                                    <small>
                                      {
                                        processingLabels[
                                          episodeProcessing.status
                                        ]
                                      }
                                    </small>
                                  </span>
                                  <StatusTag
                                    status={episodeProcessing.status}
                                    label={episodeProcessing.status}
                                    tone={processingTone(
                                      episodeProcessing.status,
                                    )}
                                  />
                                </div>
                                {episodeProcessing.status === "READY" &&
                                episodeProcessing.dataset_id &&
                                episodeProcessing.dataset_version &&
                                episodeProcessing.annotation_task_id ? (
                                  <div className={styles.processingActions}>
                                    <Link
                                      to={datasetRoutes.episodeViewer.build({
                                        datasetId:
                                          episodeProcessing.dataset_id as DatasetId,
                                        versionId:
                                          `version_lance_${episodeProcessing.dataset_version}` as DatasetVersionId,
                                        episodeId:
                                          episodeProcessing.episode_id as EpisodeId,
                                        returnTo: globalThis.location.pathname,
                                      })}
                                    >
                                      打开 Dataset Episode
                                    </Link>
                                    <Link
                                      to={annotationRoutes.task.build({
                                        taskId:
                                          episodeProcessing.annotation_task_id,
                                      })}
                                    >
                                      打开 TAGGING 任务
                                    </Link>
                                  </div>
                                ) : episodeProcessing.failure_code ? (
                                  <p className={styles.processingFailure}>
                                    <code>
                                      {episodeProcessing.failure_code}
                                    </code>
                                    <span>
                                      阶段：
                                      {episodeProcessing.failure_stage ??
                                        "未知"}
                                    </span>
                                  </p>
                                ) : (
                                  <p className={styles.processingEvidence}>
                                    {episodeProcessing.qc_report_id
                                      ? `QC ${episodeProcessing.qc_report_id}`
                                      : `工作流 ${episodeProcessing.workflow_id ?? "等待启动"}`}
                                  </p>
                                )}
                              </div>
                            ) : recording.status === "SLICED" &&
                              !processing.isPending &&
                              !processing.isError ? (
                              <Alert
                                type="warning"
                                showIcon
                                message="尚未建立处理账本"
                                description="此 Episode 不会显示为可用；请由运维检查 finalize outbox。"
                              />
                            ) : null}
                            <label>
                              标题
                              <Input
                                value={slice.title}
                                disabled={readOnly}
                                maxLength={256}
                                onChange={(event) =>
                                  updateSlices(
                                    slices.map((item) =>
                                      item.episodeId === slice.episodeId
                                        ? { ...item, title: event.target.value }
                                        : item,
                                    ),
                                  )
                                }
                              />
                            </label>
                            <label>
                              任务标签
                              <Input
                                value={slice.taskLabel}
                                disabled={readOnly}
                                maxLength={128}
                                placeholder="例如：抓取并放置"
                                onChange={(event) =>
                                  updateSlices(
                                    slices.map((item) =>
                                      item.episodeId === slice.episodeId
                                        ? {
                                            ...item,
                                            taskLabel: event.target.value,
                                          }
                                        : item,
                                    ),
                                  )
                                }
                              />
                            </label>
                            <div className={styles.boundaryFields}>
                              <label>
                                开始（秒）
                                <InputNumber
                                  value={Number(
                                    nsToSeconds(slice.startNs).toFixed(3),
                                  )}
                                  disabled={readOnly}
                                  min={0}
                                  max={nsToSeconds(slice.endNs)}
                                  step={0.033}
                                  onChange={(value) =>
                                    typeof value === "number" &&
                                    updateSlices(
                                      clampBoundary(
                                        slices,
                                        slice.episodeId,
                                        "start",
                                        secondsToNs(value),
                                        durationNs,
                                        minimumDurationNs,
                                      ),
                                    )
                                  }
                                />
                              </label>
                              <label>
                                结束（秒）
                                <InputNumber
                                  value={Number(
                                    nsToSeconds(slice.endNs).toFixed(3),
                                  )}
                                  disabled={readOnly}
                                  min={nsToSeconds(slice.startNs)}
                                  max={nsToSeconds(durationNs)}
                                  step={0.033}
                                  onChange={(value) =>
                                    typeof value === "number" &&
                                    updateSlices(
                                      clampBoundary(
                                        slices,
                                        slice.episodeId,
                                        "end",
                                        secondsToNs(value),
                                        durationNs,
                                        minimumDurationNs,
                                      ),
                                    )
                                  }
                                />
                              </label>
                            </div>
                            <label>
                              备注
                              <Input.TextArea
                                value={slice.notes}
                                disabled={readOnly}
                                rows={2}
                                maxLength={4096}
                                onChange={(event) =>
                                  updateSlices(
                                    slices.map((item) =>
                                      item.episodeId === slice.episodeId
                                        ? { ...item, notes: event.target.value }
                                        : item,
                                    ),
                                  )
                                }
                              />
                            </label>
                            <div className={styles.episodeFieldFooter}>
                              <code>{slice.episodeId}</code>
                              <Button
                                danger
                                type="text"
                                icon={<Trash2 size={15} />}
                                disabled={readOnly}
                                onClick={() => {
                                  updateSlices(
                                    slices.filter(
                                      (item) =>
                                        item.episodeId !== slice.episodeId,
                                    ),
                                  );
                                  setSelectedId(null);
                                }}
                              >
                                删除
                              </Button>
                            </div>
                            {issue ? (
                              <Typography.Text type="danger">
                                {issue.message}
                              </Typography.Text>
                            ) : null}
                          </div>
                        ) : null}
                      </article>
                    );
                  })
                )}
              </div>
            </aside>
          ),
          timelineTools: () => (
            <div className={styles.sharedTimelineTools}>
              <div className={styles.markControls}>
                <Button
                  disabled={readOnly}
                  onClick={() => setMarkInNs(currentNs)}
                >
                  设为入点 <kbd>I</kbd>
                </Button>
                <Button
                  type="primary"
                  icon={<Plus size={16} />}
                  disabled={readOnly}
                  onClick={addSlice}
                >
                  {markInNs === null ? "开始标记" : "设为出点并添加"}{" "}
                  <kbd>O</kbd>
                </Button>
                {markInNs !== null ? (
                  <span>
                    入点 {formatTimecode(markInNs)}{" "}
                    <Button
                      size="small"
                      type="link"
                      onClick={() => setMarkInNs(null)}
                    >
                      取消
                    </Button>
                  </span>
                ) : null}
              </div>
              <Tooltip title="也可以直接在共享时间轴拖拽创建或调整 Episode">
                <Button type="text" icon={<Keyboard size={16} />}>
                  拖拽选区 · I/O 快捷键
                </Button>
              </Tooltip>
            </div>
          ),
          actionDock: () => (
            <section
              className={styles.sharedActionDock}
              aria-label="切片状态与校验"
            >
              <div>
                <Scissors aria-hidden="true" size={16} />
                <span>
                  <strong>{slices.length} 条 Episode</strong>
                  <small>
                    视频、时间轴与 URDF 共用同一播放位置；拖拽选区即可调整边界。
                  </small>
                </span>
              </div>
              {issues.length > 0 ? (
                <Alert
                  type="error"
                  showIcon
                  message={`${issues.length} 个切片边界问题`}
                  description="修复重叠、越界或过短切片后才能保存。"
                />
              ) : recording.status === "SLICED" && processing.isError ? (
                <Alert
                  type="error"
                  showIcon
                  message="Episode 处理状态加载失败"
                  description={pageError(
                    processing.error,
                    "请刷新页面重新读取真实状态。",
                  )}
                  action={
                    <Button
                      size="small"
                      onClick={() => void processing.refetch()}
                    >
                      重试
                    </Button>
                  }
                />
              ) : (
                <StatusTag
                  status={dirty ? "DIRTY" : "VALID"}
                  label={dirty ? "待保存" : "边界有效"}
                  tone={dirty ? "warning" : "success"}
                />
              )}
            </section>
          ),
        }}
      />
    </div>
  );
}

export default function RecordingSegmentationPageEntry({
  gateway = recordingGateway,
  capabilityOverride,
}: Readonly<PageProps>) {
  const { recordingId } = useParams<{ recordingId: string }>();
  return recordingId ? (
    <RecordingSegmentationPage
      gateway={gateway}
      recordingId={recordingId}
      capabilityOverride={capabilityOverride}
    />
  ) : (
    <RecordingListPage gateway={gateway} />
  );
}
