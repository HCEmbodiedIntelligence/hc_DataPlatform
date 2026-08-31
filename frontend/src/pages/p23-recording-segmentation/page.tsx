import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType,
} from "antd";
import {
  ArrowLeft,
  Bot,
  Camera,
  CheckCircle2,
  Clock3,
  Keyboard,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Scissors,
  Split,
  Trash2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useToast } from "../../app/providers/ToastProvider";
import type { DatasetId } from "../../entities/dataset";
import type { DatasetVersionId } from "../../entities/dataset-version";
import type { EpisodeId } from "../../entities/episode";
import { routes as datasetRoutes } from "../../features/datasets/routing";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { StandardPageScaffold } from "../../shared/ui/layout/StandardPageScaffold";
import { PageState } from "../../shared/ui/state/PageState";
import { StatusTag } from "../../shared/ui/state/StatusTag";
import { annotationRoutes } from "../p08-data-annotation/routes";
import {
  recordingGateway,
  type ContinuousRecording,
  type EpisodeProcessing,
  type RecordingGateway,
  type RecordingScope,
} from "./api";
import { SegmentationTimeline } from "./components/SegmentationTimeline";
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
    return <StatusTag status="DRAFT" label="切片草稿" tone="warning" />;
  }
  return <StatusTag status={recording.status} label="待切片" tone="info" />;
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
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"ALL" | "READY_FOR_SLICING" | "SLICED">(
    "ALL",
  );
  const list = useQuery({
    queryKey: ["continuous-recordings", scopeKey],
    queryFn: ({ signal }) => gateway.list(scope as RecordingScope, signal),
    enabled: scope !== null,
    retry: false,
  });
  const rows = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("zh-CN");
    return (list.data?.items ?? []).filter((recording) => {
      const matchesStatus = status === "ALL" || recording.status === status;
      const matchesQuery =
        !normalized ||
        [
          recording.recording_id,
          recording.rollout_id,
          recording.robot_id,
          recording.collection_task_id,
        ].some((value) =>
          value.toLocaleLowerCase("zh-CN").includes(normalized),
        );
      return matchesStatus && matchesQuery;
    });
  }, [list.data?.items, query, status]);

  const columns = useMemo<TableColumnsType<ContinuousRecording>>(
    () => [
      {
        title: "录制",
        key: "recording",
        render: (_, recording) => (
          <div className={styles.recordingIdentity}>
            <Link
              to={`/recordings/${encodeURIComponent(recording.recording_id)}/slice`}
            >
              {recording.recording_id}
            </Link>
            <small>
              机器人 {recording.robot_id} · 任务 {recording.collection_task_id}
            </small>
          </div>
        ),
      },
      {
        title: "采集时间",
        key: "captured",
        responsive: ["md"],
        render: (_, recording) => (
          <div className={styles.recordingTime}>
            <span>
              {new Intl.DateTimeFormat("zh-CN", {
                dateStyle: "medium",
                timeStyle: "short",
              }).format(new Date(recording.capture_started_at))}
            </span>
            <small>
              <Clock3 size={13} aria-hidden="true" />
              {formatDuration(durationOf(recording))}
            </small>
          </div>
        ),
      },
      {
        title: "原始视频",
        dataIndex: "video_asset_count",
        key: "videos",
        width: 112,
        render: (count: number) => (
          <span className={styles.numeric}>{count} 路</span>
        ),
      },
      {
        title: "版本",
        key: "revision",
        width: 108,
        responsive: ["lg"],
        render: (_, recording) => (
          <span className={styles.numeric}>r{recording.current_revision}</span>
        ),
      },
      {
        title: "状态",
        key: "status",
        width: 120,
        render: (_, recording) => statusTag(recording),
      },
      {
        title: "操作",
        key: "action",
        width: 116,
        render: (_, recording) => (
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
                ? "继续切片"
                : "开始切片"}
          </Button>
        ),
      },
    ],
    [navigate],
  );

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
  } else if (rows.length === 0) {
    state = (
      <PageState
        state={query || status !== "ALL" ? "filtered-empty" : "empty"}
        label="录制列表"
      />
    );
  }

  return (
    <StandardPageScaffold
      header={{
        title: "录制切片",
        actions: (
          <Button
            icon={<RefreshCw size={16} />}
            loading={list.isFetching}
            onClick={() => void list.refetch()}
          >
            刷新
          </Button>
        ),
      }}
      summary={
        <div className={styles.summaryStrip}>
          <span>
            <strong>{list.data?.total ?? 0}</strong> 条录制
          </span>
          <span>
            <strong>
              {list.data?.items.filter(
                (item) => item.status === "READY_FOR_SLICING",
              ).length ?? 0}
            </strong>{" "}
            条待切片
          </span>
          <span>
            <strong>
              {list.data?.items.reduce(
                (sum, item) => sum + item.video_asset_count,
                0,
              ) ?? 0}
            </strong>{" "}
            路原始视频
          </span>
        </div>
      }
      filters={
        <div className={styles.listToolbar}>
          <Input.Search
            allowClear
            aria-label="搜索录制"
            placeholder="搜索录制、机器人或采集任务"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select
            aria-label="按切片状态筛选"
            value={status}
            onChange={setStatus}
            options={[
              { value: "ALL", label: "全部状态" },
              { value: "READY_FOR_SLICING", label: "待切片" },
              { value: "SLICED", label: "已成片" },
            ]}
          />
        </div>
      }
      state={state}
    >
      <div className={styles.listPanel}>
        <Table
          rowKey="recording_id"
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          scroll={{ x: 780 }}
        />
      </div>
    </StandardPageScaffold>
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
  const [activeCameraId, setActiveCameraId] = useState<string | null>(null);
  const [currentNs, setCurrentNs] = useState(0);
  const [markInNs, setMarkInNs] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [dirty, setDirty] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const initializedEtag = useRef<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const recording = detail.data?.data;
  const revision = detail.data?.current_slice_revision ?? null;
  const durationNs = recording ? durationOf(recording) : 0;
  const activeSource =
    sources.data?.sources.find(
      (source) => source.camera_id === activeCameraId,
    ) ?? sources.data?.sources[0];
  const minimumDurationNs = Math.max(
    1,
    Math.round(1_000_000_000 / (activeSource?.fps ?? 30)),
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

  useEffect(() => {
    if (!recording || initializedEtag.current === recording.etag) return;
    const initial = fromServerSlices(revision?.slices ?? []);
    setSlices(initial);
    setSelectedId(initial[0]?.episodeId ?? null);
    setDirty(false);
    initializedEtag.current = recording.etag;
  }, [recording, revision]);

  useEffect(() => {
    const firstCamera = sources.data?.sources[0]?.camera_id;
    if (firstCamera && !activeCameraId) setActiveCameraId(firstCamera);
  }, [activeCameraId, sources.data?.sources]);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    globalThis.addEventListener("beforeunload", warn);
    return () => globalThis.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const seek = useCallback(
    (valueNs: number) => {
      const next = Math.min(durationNs, Math.max(0, Math.round(valueNs)));
      setCurrentNs(next);
      if (videoRef.current) videoRef.current.currentTime = nsToSeconds(next);
    },
    [durationNs],
  );

  const updateSlices = useCallback((next: readonly EditableSlice[]) => {
    setSlices([...next]);
    setDirty(true);
  }, []);

  const togglePlayback = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      try {
        await video.play();
      } catch {
        showToast({
          title: "视频未能开始播放",
          message: "请检查原视频编码或授权地址是否仍有效。",
          tone: "error",
        });
      }
    } else {
      video.pause();
    }
  }, [showToast]);

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
    if (target.matches("input, textarea, select, [contenteditable=true]"))
      return;
    const frameNs = minimumDurationNs;
    if (event.key === " ") {
      event.preventDefault();
      void togglePlayback();
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
      <div className={styles.workbenchHeader}>
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
            <h1>{recording.recording_id}</h1>
            <p>
              {formatDuration(durationNs)} · {recording.video_asset_count}{" "}
              路相机 · 机器人 {recording.robot_id}
            </p>
          </div>
        </div>
        <div className={styles.headerActions}>
          {dirty ? (
            <Tag color="warning">有未保存修改</Tag>
          ) : (
            <Tag color="default">已保存 r{recording.current_revision}</Tag>
          )}
          <Button
            icon={<Save size={16} />}
            disabled={
              readOnly || (!dirty && !needsHumanReview) || issues.length > 0
            }
            loading={saveDraft.isPending}
            onClick={() => saveDraft.mutate()}
          >
            {needsHumanReview ? "确认并保存人工草稿" : "保存草稿"}
          </Button>
          <Tooltip
            title={
              !finalizeAllowed ? "先保存人工草稿并修复所有边界问题" : undefined
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
      </div>

      {recording.status === "SLICED" ? (
        <Alert
          type="success"
          showIcon
          message="切片已最终提交"
          description="这些 Episode 已进入质检与对齐流程；当前页面以只读方式保留原视频和时间边界。"
        />
      ) : revision?.authoring_mode === "MODEL" ? (
        <Alert
          type="warning"
          showIcon
          icon={<Bot size={18} />}
          message="当前是大模型切片建议"
          description="请逐条核对边界。任意人工保存都会形成新的 HUMAN 草稿，之后才可提交成片。"
        />
      ) : !canManage ? (
        <Alert
          type="info"
          showIcon
          message="当前为只读模式"
          description="需要数据上传管理或标注编辑权限才能修改并提交切片。"
        />
      ) : null}

      {recording.status === "SLICED" && processing.isError ? (
        <Alert
          type="error"
          showIcon
          message="Episode 处理状态加载失败"
          description={pageError(
            processing.error,
            "数据仍在后台处理；请刷新页面重新读取真实状态。",
          )}
          action={
            <Button size="small" onClick={() => void processing.refetch()}>
              重试
            </Button>
          }
        />
      ) : null}

      <div className={styles.editorGrid}>
        <section className={styles.videoWorkspace} aria-label="原始视频预览">
          <div className={styles.panelHeading}>
            <div>
              <Camera size={17} aria-hidden="true" />
              <strong>原始视频</strong>
              <span>直接读取 OSS，不生成预览副本</span>
            </div>
            {sources.data?.sources.length ? (
              <div
                className={styles.cameraTabs}
                role="tablist"
                aria-label="选择相机"
              >
                {sources.data.sources.map((source) => (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={source.asset_id === activeSource?.asset_id}
                    key={source.asset_id}
                    onClick={() => setActiveCameraId(source.camera_id)}
                  >
                    {source.camera_id}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <div className={styles.videoStage}>
            {sources.isPending ? (
              <PageState state="loading" layout="section" label="原视频授权" />
            ) : null}
            {sources.isError ? (
              <PageState
                state="error"
                title="原视频暂时无法播放"
                description={pageError(
                  sources.error,
                  "请检查是否为 v2 视频录制，或刷新短期授权地址。",
                )}
                onRetry={() => void sources.refetch()}
              />
            ) : null}
            {recording.schema_version !== "continuous-recording/v2" ? (
              <PageState
                state="feature-unavailable"
                title="此录制没有独立原视频"
                description="只有 v2 多对象录制能在切片前直接播放 OSS 原视频。"
              />
            ) : null}
            {activeSource ? (
              <video
                ref={videoRef}
                key={activeSource.asset_id}
                className={styles.video}
                src={activeSource.source_url}
                preload="metadata"
                playsInline
                onLoadedMetadata={(event) => {
                  event.currentTarget.currentTime = nsToSeconds(currentNs);
                  setVideoFailed(false);
                }}
                onTimeUpdate={(event) =>
                  setCurrentNs(secondsToNs(event.currentTarget.currentTime))
                }
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => setPlaying(false)}
                onError={() => setVideoFailed(true)}
              />
            ) : null}
            {videoFailed ? (
              <div className={styles.videoError} role="alert">
                浏览器无法解码此原视频，可能需要转封装为支持 Range 的 MP4。
              </div>
            ) : null}
          </div>
          <div className={styles.transportBar}>
            <Button
              shape="circle"
              type="primary"
              icon={playing ? <Pause size={16} /> : <Play size={16} />}
              onClick={() => void togglePlayback()}
              disabled={!activeSource}
              aria-label={playing ? "暂停" : "播放"}
            />
            <span className={styles.timecode}>
              {formatTimecode(currentNs)}{" "}
              <small>/ {formatTimecode(durationNs)}</small>
            </span>
            <input
              type="range"
              aria-label="视频播放位置"
              min={0}
              max={durationNs || 1}
              step={minimumDurationNs}
              value={Math.min(currentNs, durationNs)}
              onChange={(event) => seek(Number(event.target.value))}
            />
            <span className={styles.sourceFacts}>
              {activeSource
                ? `${activeSource.fps} fps · ${activeSource.codec}`
                : "等待视频"}
            </span>
          </div>
        </section>

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
                            label={processingLabels[episodeProcessing.status]}
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
                                  {processingLabels[episodeProcessing.status]}
                                </small>
                              </span>
                              <StatusTag
                                status={episodeProcessing.status}
                                label={episodeProcessing.status}
                                tone={processingTone(episodeProcessing.status)}
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
                                <code>{episodeProcessing.failure_code}</code>
                                <span>
                                  阶段：
                                  {episodeProcessing.failure_stage ?? "未知"}
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
                                    ? { ...item, taskLabel: event.target.value }
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
      </div>

      <div className={styles.sliceToolbar}>
        <div className={styles.markControls}>
          <Button disabled={readOnly} onClick={() => setMarkInNs(currentNs)}>
            设为入点 <kbd>I</kbd>
          </Button>
          <Button
            type="primary"
            icon={<Plus size={16} />}
            disabled={readOnly}
            onClick={addSlice}
          >
            {markInNs === null ? "开始标记" : "设为出点并添加"} <kbd>O</kbd>
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
        <Space size="small">
          <Tooltip title="空格播放/暂停；左右键逐帧；Shift+左右键跳 1 秒">
            <Button type="text" icon={<Keyboard size={16} />}>
              快捷键
            </Button>
          </Tooltip>
          <Select
            aria-label="时间轴缩放"
            value={zoom}
            onChange={setZoom}
            options={[1, 4, 16, 64, 256].map((value) => ({
              value,
              label: `${value}×`,
            }))}
          />
        </Space>
      </div>
      <SegmentationTimeline
        durationNs={durationNs}
        currentNs={currentNs}
        markInNs={markInNs}
        slices={slices}
        selectedId={selectedId}
        zoom={zoom}
        disabled={readOnly}
        minimumDurationNs={minimumDurationNs}
        onSeek={seek}
        onSelect={setSelectedId}
        onChange={updateSlices}
      />
      {issues.length > 0 ? (
        <Alert
          type="error"
          showIcon
          message={`${issues.length} 个切片边界问题`}
          description="修复重叠、越界或过短切片后才能保存。"
        />
      ) : null}
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
