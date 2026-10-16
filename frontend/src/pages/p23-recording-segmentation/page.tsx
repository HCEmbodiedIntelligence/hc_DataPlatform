import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Drawer,
  Input,
  InputNumber,
  Modal,
  Pagination,
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
  X,
} from "lucide-react";
import {
  lazy,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
const DatasetSourceFiles = lazy(
  () => import("../../features/ingest/browser/DatasetSourceFiles"),
);
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { EpisodePreview } from "./EpisodePreview";
import { RecordingNavigator, type RecordingFilter } from "./RecordingNavigator";
import {
  recordingFileName,
  recordingRobotName,
  recordingTaskNumber,
  recordingSearchValues,
} from "./presentation";
import { useToast } from "../../shared/ui/ToastProvider";
import {
  useScopedRobotModelAssets,
  useScopedRobotModelJointMappings,
  useScopedRobotModelVersion,
} from "../../features/robot-models/api/hooks";
import { authorizeRobotModelViewerAssets } from "../../features/robot-models/api/client";
import { useScopedRobotBootstrap } from "../../features/robots/api/hooks";
import {
  buildJointFrameSource,
  createLazyThreeRobotSceneLoader,
  createPlaybackClock,
  CameraViewToolbar,
  selectCameraStreams,
  SynchronizedEpisodeWorkbench,
} from "../../features/viewer";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { StandardPageScaffold } from "../../shared/ui/layout/StandardPageScaffold";
import { PageState } from "../../shared/ui/state/PageState";
import { StatusTag } from "../../shared/ui/state/StatusTag";
import { WorkflowQueuePage } from "../../shared/ui/workflow/WorkflowQueuePage";
import {
  recordingGateway,
  type ContinuousRecording,
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
  const page = Math.max(1, Number(searchParams.get("page")) || 1);
  const pageSize = 10;
  const stage: RecordingQueueStage =
    requestedStage === "REVIEW" || requestedStage === "SLICED"
      ? requestedStage
      : "PENDING";
  const list = useQuery({
    queryKey: ["continuous-recordings", scopeKey],
    queryFn: ({ signal }) => gateway.list(scope as RecordingScope, signal),
    enabled: scope !== null,
    retry: false,
    refetchInterval: 5_000,
  });
  const rows = useMemo(() => {
    const normalized = deferredQuery.trim().toLocaleLowerCase("zh-CN");
    return (list.data?.items ?? [])
      .filter((recording) => {
        if (recordingQueueStage(recording) !== stage) return false;
        if (!normalized) return true;
        return recordingSearchValues(recording).some((value) =>
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
    next.delete("page");
    if (normalized) next.set("q", normalized);
    else next.delete("q");
    setSearchParams(next, { replace: true });
  };

  const stageHref = (nextStage: RecordingQueueStage) => {
    const next = new URLSearchParams(searchParams);
    next.delete("page");
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
      density="compact"
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
          description: "查看已生成的 Episode",
          queueDescription: "按片段查看与播放已生成的 Episode。",
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
        label: "搜索文件名、机器人名称或任务编号",
        placeholder: "搜索文件名 / 机器人 / 任务编号…",
        value: query,
        meta: `${counts[stage]} 条处于此状态 · 共 ${list.data?.total ?? 0} 条录制`,
        onChange: updateQuery,
      }}
      pagination={
        <Pagination
          current={Math.min(
            page,
            Math.max(1, Math.ceil(rows.length / pageSize)),
          )}
          total={rows.length}
          pageSize={pageSize}
          showSizeChanger={false}
          onChange={(value) => {
            const next = new URLSearchParams(searchParams);
            next.set("page", String(value));
            setSearchParams(next);
          }}
        />
      }
      tableLabel="录制表"
      table={
        <table>
          <caption>{stage} 录制列表</caption>
          <thead>
            <tr>
              <th>录制文件名</th>
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
            {rows
              .slice(
                (Math.min(
                  page,
                  Math.max(1, Math.ceil(rows.length / pageSize)),
                ) -
                  1) *
                  pageSize,
                Math.min(page, Math.max(1, Math.ceil(rows.length / pageSize))) *
                  pageSize,
              )
              .map((recording) => (
                <tr
                  key={recording.recording_id}
                  className={styles.recordingRow}
                  tabIndex={0}
                  aria-label={`打开录制 ${recordingFileName(recording)}`}
                  onClick={(event) => {
                    if (
                      !(event.target as HTMLElement).closest("a,button,input")
                    )
                      navigate(
                        `/recordings/${encodeURIComponent(recording.recording_id)}/slice`,
                      );
                  }}
                  onKeyDown={(event) => {
                    if (
                      event.target === event.currentTarget &&
                      (event.key === "Enter" || event.key === " ")
                    ) {
                      event.preventDefault();
                      navigate(
                        `/recordings/${encodeURIComponent(recording.recording_id)}/slice`,
                      );
                    }
                  }}
                >
                  <th scope="row">
                    <span
                      className={styles.recordingPrimary}
                      title={recordingFileName(recording)}
                    >
                      {recordingFileName(recording)}
                    </span>
                  </th>
                  <td>{statusTag(recording)}</td>
                  <td>
                    <span
                      className={styles.recordingPrimary}
                      title={recordingRobotName(recording)}
                    >
                      {recordingRobotName(recording)}
                    </span>
                    <small>{recordingTaskNumber(recording)}</small>
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
  recordingFilter,
  onRecordingFilterChange,
  navigatorCollapsed,
  onNavigatorToggle,
}: {
  readonly gateway: RecordingGateway;
  readonly recordingId: string;
  readonly capabilityOverride?: "manage" | "read-only";
  readonly recordingFilter: RecordingFilter;
  readonly onRecordingFilterChange: (next: RecordingFilter) => void;
  readonly navigatorCollapsed: boolean;
  readonly onNavigatorToggle: () => void;
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
  const [slices, setSlices] = useState<EditableSlice[]>([]);
  const [episodeSearch] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    episodeSearch.get("episode"),
  );
  const [currentNs, setCurrentNs] = useState(0);
  const [markInNs, setMarkInNs] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [cameraView, setCameraView] = useState("__quad__");
  const [previewSlice, setPreviewSlice] = useState<EditableSlice | null>(null);
  const [originalFilesOpen, setOriginalFilesOpen] = useState(false);
  const [finalizedNoticeClosed, setFinalizedNoticeClosed] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<string | null>(
    null,
  );
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
  const robotBootstrap = useScopedRobotBootstrap(
    scope?.organizationId ?? null,
    recording?.robot_id ?? null,
  );
  const boundVersionId =
    robotBootstrap.data?.effectiveModelBinding?.robotModelVersionId ?? null;
  const robotModelVersion = useScopedRobotModelVersion(
    scope?.organizationId ?? null,
    boundVersionId,
  );
  const robotModelAssets = useScopedRobotModelAssets(
    scope?.organizationId ?? null,
    boundVersionId,
  );
  const robotJointMappings = useScopedRobotModelJointMappings(
    scope?.organizationId ?? null,
    boundVersionId,
  );
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
      return `正在解析机器人 ${recordingRobotName(recording)} 的模型绑定…`;
    if (robotBootstrap.isError)
      return `无法读取机器人 ${recordingRobotName(recording)} 的模型配置。`;
    if (!boundVersionId)
      return `机器人 ${recordingRobotName(recording)} 尚未绑定已发布 URDF 模型。`;
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
  const visibleCameraStreams = useMemo(
    () => selectCameraStreams(cameraStreams, cameraView, clock),
    [cameraStreams, cameraView, clock],
  );
  const selectedSlice = slices.find((slice) => slice.episodeId === selectedId);

  useEffect(() => {
    if (!recording || initializedEtag.current === recording.etag) return;
    const initial = fromServerSlices(revision?.slices ?? []);
    setSlices(initial);
    setSelectedId((previous) =>
      initial.some((slice) => slice.episodeId === previous)
        ? previous
        : (initial[0]?.episodeId ?? null),
    );
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
    (rawStartNs: string, rawEndNs: string, create = false) => {
      if (readOnly) return;
      const startNs = Math.max(0, Number(rawStartNs));
      const endNs = Math.min(durationNs, Number(rawEndNs));
      if (
        !Number.isSafeInteger(startNs) ||
        !Number.isSafeInteger(endNs) ||
        endNs - startNs < minimumDurationNs
      )
        return;

      const selected = create
        ? undefined
        : slices.find((slice) => slice.episodeId === selectedId);
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
    mutationFn: async () => {
      let current = { recording: recording!, revision };
      if (dirty || !revision || slices.length === 0) {
        const submission = slices.length
          ? slices
          : [
              {
                episodeId: nextEpisodeId(slices),
                startNs: 0,
                endNs: durationNs,
                title: "完整录制",
                taskLabel: "",
                notes: "",
              },
            ];
        const saved = await gateway.saveDraft(
          scope as RecordingScope,
          recordingId,
          { slices: toSliceInputs(submission) },
          recording!.etag,
        );
        // Keep the committed draft/ETag if finalization fails so retry is safe.
        current = saved;
        initializedEtag.current = saved.recording.etag;
        setSlices(fromServerSlices(saved.revision.slices));
        setSelectedId(saved.revision.slices[0]?.episode_id ?? null);
        setDirty(false);
        queryClient.setQueryData(detailKey, {
          data: saved.recording,
          current_slice_revision: saved.revision,
        });
      }
      return gateway.finalize(
        scope as RecordingScope,
        recordingId,
        current.revision!.revision,
        current.recording.etag,
      );
    },
    onSuccess: (result) => {
      initializedEtag.current = result.recording.etag;
      setSlices(fromServerSlices(result.revision.slices));
      setSelectedId(result.revision.slices[0]?.episode_id ?? null);
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
        message: `已生成 ${result.revision.slices.length} 条 Episode。`,
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

  const navigateFromRecording = (path: string) => {
    if (saveDraft.isPending || finalize.isPending) return;
    if (!dirty) {
      navigate(path);
      return;
    }
    setPendingNavigation(path);
  };
  const leaveToList = () => navigateFromRecording("/recordings");

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
            { key: "recording", label: "录制详情" },
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
    durationNs >= minimumDurationNs &&
    issues.length === 0 &&
    revision?.authoring_mode !== "MODEL" &&
    !saveDraft.isPending;
  const needsHumanReview = revision?.authoring_mode === "MODEL";
  const timelineTracks = buildEpisodeTimelineTracks(slices).map((track) => ({
    ...track,
    segments: track.segments?.map((segment) => ({
      ...segment,
      editing: readOnly
        ? undefined
        : {
            selected: selectedId === segment.id,
            onSelect: () => setSelectedId(segment.id),
            onChange: (startNs: string, endNs: string) =>
              updateSlices(
                slices.map((slice) =>
                  slice.episodeId === segment.id
                    ? {
                        ...slice,
                        startNs: Number(startNs),
                        endNs: Number(endNs),
                      }
                    : slice,
                ),
              ),
            onDelete: () => {
              updateSlices(
                slices.filter((slice) => slice.episodeId !== segment.id),
              );
              setSelectedId(null);
            },
          },
    })),
  }));
  const renderEpisodeList = () => (
    <aside className={styles.episodeInspector} aria-label="Episode 列表与属性">
      <div className={styles.episodeList}>
        {slices.length === 0 ? (
          <div className={styles.emptyEpisodes}>
            <Scissors size={28} aria-hidden="true" />
            <strong>还没有 Episode</strong>
            <span>
              不分段可直接提交完整录制；需要多段时，在时间轴空白处拖拽新增
              Episode。
            </span>
          </div>
        ) : (
          slices.map((slice, index) => {
            const selected = selectedId === slice.episodeId;
            const issue = issues.find(
              (item) => item.episodeId === slice.episodeId,
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
                    {issue ? <Tag color="error">边界错误</Tag> : null}
                  </span>
                </button>
                {selected ? (
                  <div className={styles.episodeFields}>
                    <label>
                      标题
                      <Input
                        size="small"
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
                        size="small"
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
                          size="small"
                          value={Number(nsToSeconds(slice.startNs).toFixed(3))}
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
                          size="small"
                          value={Number(nsToSeconds(slice.endNs).toFixed(3))}
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
                    <label className={styles.episodeNotes}>
                      备注
                      <Input.TextArea
                        size="small"
                        value={slice.notes}
                        disabled={readOnly}
                        autoSize={{ minRows: 1, maxRows: 3 }}
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
                      <Button
                        size="small"
                        onClick={() => {
                          clock.pause();
                          setPreviewSlice(slice);
                        }}
                      >
                        查看视频
                      </Button>
                      <Button
                        size="small"
                        danger
                        type="text"
                        icon={<Trash2 size={15} />}
                        disabled={readOnly}
                        onClick={() => {
                          updateSlices(
                            slices.filter(
                              (item) => item.episodeId !== slice.episodeId,
                            ),
                          );
                          setSelectedId(null);
                        }}
                      >
                        删除
                      </Button>
                    </div>
                    {issue ? (
                      <Typography.Text
                        className={styles.episodeBoundaryError}
                        type="danger"
                      >
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
  );

  return (
    <div
      className={styles.recordingWorkspace}
      data-navigator-collapsed={navigatorCollapsed}
      tabIndex={0}
      onKeyDown={handleKeyboard}
      aria-label="录制切片工作台"
    >
      <RecordingNavigator
        gateway={gateway}
        scope={scope}
        scopeKey={scopeKey}
        recording={recording}
        collapsed={navigatorCollapsed}
        onToggle={onNavigatorToggle}
        filter={recordingFilter}
        onFilterChange={onRecordingFilterChange}
        disabled={saveDraft.isPending || finalize.isPending}
        onSelect={(id) => {
          if (id !== recordingId)
            navigateFromRecording(
              `/recordings/${encodeURIComponent(id)}/slice`,
            );
        }}
      />
      <div className={styles.workbenchPage}>
        <SynchronizedEpisodeWorkbench
          jointAngleStream={rawJointStream}
          adapter={{
            mode: "episode-slicing",
            id: `recording-slicing-${recording.recording_id}`,
            title: "录制切片",
            description: `${recordingFileName(recording)} · ${formatDuration(durationNs)} · ${recording.video_asset_count} 路相机 · ${recordingRobotName(recording)} · 任务 ${recordingTaskNumber(recording)}`,
            readOnly,
            clock,
            cameraStreams: visibleCameraStreams,
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
            onTimeRangeCreate: readOnly
              ? undefined
              : (start, end) => applyTimelineRange(start, end, true),
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
                          event.preventDefault();
                          leaveToList();
                        }}
                      >
                        录制切片
                      </Link>
                      <span>/</span>
                      <span
                        aria-current="page"
                        title={recordingFileName(recording)}
                      >
                        {recordingFileName(recording)}
                      </span>
                    </nav>
                    <p>
                      直接读取存储中的原视频与原始关节角；视频、时间轴与 URDF
                      使用同一录制时钟。
                    </p>
                  </div>
                </div>
                <div className={styles.headerActions}>
                  {capabilities.has("upload.read") && (
                    <Button onClick={() => setOriginalFilesOpen(true)}>
                      原始文件
                    </Button>
                  )}
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
                      readOnly || finalize.isPending || issues.length > 0
                    }
                    loading={saveDraft.isPending}
                    onClick={() => saveDraft.mutate()}
                  >
                    {needsHumanReview ? "确认并保存人工草稿" : "保存草稿"}
                  </Button>
                  <Tooltip
                    title={
                      !finalizeAllowed
                        ? "请先确认模型建议并修复边界问题"
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
                          title: slices.length
                            ? `生成 ${slices.length} 条 Episode？`
                            : "将完整录制生成为 1 条 Episode？",
                          content:
                            "无需填写任务标签。将保存切片区间并生成 Episode；提交后区间锁定，原始录制保留。",
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
                {recording.status === "SLICED" && !finalizedNoticeClosed ? (
                  <div className={styles.finalizedNotice} role="status">
                    <CheckCircle2 size={15} aria-hidden="true" />
                    <span>切片已最终提交</span>
                    <span className={styles.finalizedNoticeDetail}>
                      · 已生成 Episode，区间只读
                    </span>
                    <Button
                      type="text"
                      size="small"
                      aria-label="关闭提交提示"
                      icon={<X size={14} />}
                      onClick={() => setFinalizedNoticeClosed(true)}
                    />
                  </div>
                ) : recording.status !== "SLICED" && !canManage ? (
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
              <CameraViewToolbar
                view={cameraView}
                cameras={cameraStreams.map((stream) => ({
                  id: stream.id,
                  label: stream.displayName,
                }))}
                visibleStreams={visibleCameraStreams}
                onViewChange={setCameraView}
              >
                {sources.isPending ? <span>正在授权视频…</span> : null}
              </CameraViewToolbar>
            ),
            timelineTools: () => (
              <>
                <div className={styles.sharedTimelineTools}>
                  <div className={styles.markControls}>
                    <span className={styles.episodeSummary}>
                      <Scissors size={15} aria-hidden="true" />
                      <strong>Episode</strong>
                      <Tag>{slices.length}</Tag>
                    </span>
                    <Button
                      size="small"
                      disabled={readOnly}
                      onClick={() => {
                        setSelectedId(null);
                        setMarkInNs(null);
                      }}
                    >
                      新增 Episode
                    </Button>
                    <Button
                      size="small"
                      disabled={readOnly}
                      onClick={() => setMarkInNs(currentNs)}
                    >
                      设为入点 <kbd>I</kbd>
                    </Button>
                    <Button
                      size="small"
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

                  <Button
                    size="small"
                    icon={<Split size={14} />}
                    disabled={readOnly || !selectedId}
                    onClick={splitSelected}
                  >
                    播放头拆分
                  </Button>
                  <StatusTag
                    status={
                      issues.length ? "INVALID" : dirty ? "DIRTY" : "VALID"
                    }
                    label={
                      issues.length
                        ? `${issues.length} 个边界问题`
                        : dirty
                          ? "待保存"
                          : "边界有效"
                    }
                    tone={
                      issues.length ? "danger" : dirty ? "warning" : "success"
                    }
                  />
                  <Tooltip title="拖拽创建或调整 Episode；I 设入点，O 设出点并添加，空格播放或暂停">
                    <Button
                      size="small"
                      type="text"
                      aria-label="切片快捷键"
                      icon={<Keyboard size={16} />}
                    />
                  </Tooltip>
                </div>
                {renderEpisodeList()}
              </>
            ),
          }}
        />
      </div>
      {previewSlice && (
        <EpisodePreview
          slice={previewSlice}
          streams={cameraStreams}
          onClose={() => setPreviewSlice(null)}
        />
      )}
      <Modal
        title="当前录制有未保存的切片修改"
        open={pendingNavigation !== null}
        closable={!saveDraft.isPending}
        keyboard={!saveDraft.isPending}
        mask={{ closable: false }}
        onCancel={() => {
          if (!saveDraft.isPending) setPendingNavigation(null);
        }}
        footer={
          <>
            <Button
              danger
              disabled={saveDraft.isPending}
              onClick={() => {
                if (pendingNavigation) navigate(pendingNavigation);
              }}
            >
              放弃修改并切换
            </Button>
            <Button
              disabled={saveDraft.isPending}
              onClick={() => setPendingNavigation(null)}
            >
              继续编辑
            </Button>
            <Button
              type="primary"
              loading={saveDraft.isPending}
              disabled={issues.length > 0 || saveDraft.isPending}
              onClick={() =>
                saveDraft.mutate(undefined, {
                  onSuccess: () => {
                    if (pendingNavigation) navigate(pendingNavigation);
                  },
                })
              }
            >
              保存草稿并切换
            </Button>
          </>
        }
      >
        <p>
          {issues.length > 0
            ? "当前切片边界有问题，请返回修复后保存，或放弃本次修改再切换。"
            : "保存草稿后切换，可以稍后回来继续编辑。"}
        </p>
        {saveDraft.isError && (
          <Alert type="error" title="保存失败，仍停留在当前录制，修改已保留" />
        )}
      </Modal>
      <Drawer
        title="原始文件"
        size="large"
        styles={{ wrapper: { maxWidth: "100vw" } }}
        open={originalFilesOpen}
        onClose={() => setOriginalFilesOpen(false)}
        destroyOnHidden
      >
        {originalFilesOpen && scope && capabilities.has("upload.read") && (
          <div onKeyDown={(event) => event.stopPropagation()}>
            <Suspense fallback={<p>加载原始文件…</p>}>
              <DatasetSourceFiles
                key={scopeKey + recordingId}
                scope={scope}
                recordingId={recordingId}
              />
            </Suspense>
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default function RecordingSegmentationPageEntry({
  gateway = recordingGateway,
  capabilityOverride,
}: Readonly<PageProps>) {
  const { recordingId } = useParams<{ recordingId: string }>();
  const entryScope = useShellStore((state) => state.scope);
  const [navigatorCollapsed, setNavigatorCollapsed] = useState(false);
  const [recordingFilter, setRecordingFilter] = useState<RecordingFilter>({
    search: "",
    sameTask: true,
    stage: "all",
  });
  return recordingId ? (
    <RecordingSegmentationPage
      key={`${entryScope?.organizationId}:${entryScope?.projectId}:${entryScope?.regionCode}:${recordingId}`}
      gateway={gateway}
      recordingId={recordingId}
      capabilityOverride={capabilityOverride}
      recordingFilter={recordingFilter}
      onRecordingFilterChange={setRecordingFilter}
      navigatorCollapsed={navigatorCollapsed}
      onNavigatorToggle={() => setNavigatorCollapsed((value) => !value)}
    />
  ) : (
    <RecordingListPage gateway={gateway} />
  );
}
