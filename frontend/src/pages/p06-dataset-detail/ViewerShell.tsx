import { ResourceLabel } from "../../shared/lib/resource-labels";
import { describeEpisodeRobotPreview } from "../../features/datasets/episodes/robot-preview-provenance";
import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Drawer, Typography } from "antd";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { isDatasetId, type DatasetId } from "../../entities/dataset";
import {
  isDatasetVersionId,
  type DatasetVersionId,
} from "../../entities/dataset-version";
import { isEpisodeId, type EpisodeId } from "../../entities/episode";
import {
  SynchronizedEpisodeWorkbench,
  CameraViewToolbar,
  selectCameraStreams,
  usePlaybackClock,
} from "../../features/viewer";
import { useViewerEpisodeQuery } from "../../features/datasets/api";
import { RegionState } from "../../features/datasets/components/RegionState";
import { datasetRegionStateForError } from "../../features/datasets/components/error-state";
import { routes } from "../../features/datasets/routing";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState } from "../../shared/ui";
import { useLogicalRobotScene } from "../../features/datasets/episodes/use-logical-robot-scene";
import { assetEpisodeViewerQueryCodec } from "./query-codec";
import { EpisodeDetailPanel } from "./EpisodeDetailPanel";
import { reviewDataReference } from "../../features/datasets/episodes/data-reviews";
import { selectEpisodeJointStream } from "../../features/datasets/episodes/logical-viewer";
import { useLogicalViewer } from "../../features/datasets/episodes/use-logical-viewer";
import { useEpisodeAnnotations } from "./use-episode-annotations";
import { EpisodeTagSummary } from "./EpisodeTagSummary";
import styles from "./styles.module.css";

const invalidDataset = "dataset_invalid" as DatasetId;
const invalidVersion = "version_invalid" as DatasetVersionId;
const invalidEpisode = "episode_invalid" as EpisodeId;

function decimalSecondsToNs(value: string): string | null {
  if (!/^(0|[1-9][0-9]*)(\.[0-9]{1,9})?$/.test(value)) return null;
  const [seconds = "0", fraction = ""] = value.split(".");
  return (
    BigInt(seconds) * 1_000_000_000n +
    BigInt(fraction.padEnd(9, "0") || "0")
  ).toString();
}

export function EpisodeViewerShell() {
  const raw = useParams();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const search = assetEpisodeViewerQueryCodec.parse(params);
  const capabilities = useCapabilities();
  const scope = useShellStore((state) => state.scope);
  const valid =
    isDatasetId(raw.datasetId) &&
    isDatasetVersionId(raw.versionId) &&
    isEpisodeId(raw.episodeId);
  const datasetId = valid ? (raw.datasetId as DatasetId) : invalidDataset;
  const versionId = valid
    ? (raw.versionId as DatasetVersionId)
    : invalidVersion;
  const episodeId = valid ? (raw.episodeId as EpisodeId) : invalidEpisode;
  const query = useViewerEpisodeQuery(
    datasetId,
    versionId,
    episodeId,
    valid && capabilities.has("episode.read"),
  );
  const [dataInfoOpen, setDataInfoOpen] = useState(false);
  const [dataInfoTab, setDataInfoTab] = useState("playback");
  const [cameraView, setCameraView] = useState("__quad__");

  const revision = query.data?.revision;
  const reviewTarget = useMemo(() => {
    try {
      return {
        data: reviewDataReference(
          revision?.streams.flatMap((stream) =>
            stream.package_binding ? [stream.package_binding.data] : [],
          ) ?? [],
        ),
        error: null,
      };
    } catch (error) {
      return {
        data: null,
        error: error instanceof Error ? error.message : "数据版本绑定异常。",
      };
    }
  }, [revision]);
  const startNs = revision?.started_at_ns ?? "0";
  const endNs = revision
    ? (BigInt(revision.started_at_ns) + BigInt(revision.duration_ns)).toString()
    : "1";
  const clock = usePlaybackClock(
    startNs,
    BigInt(endNs) > BigInt(startNs) ? endNs : (BigInt(startNs) + 1n).toString(),
    `${datasetId}:${versionId}:${episodeId}`,
  );
  useEffect(() => {
    if (!search.t) return;
    const value = decimalSecondsToNs(search.t);
    if (value !== null) clock.seek(value);
  }, [clock, search.t]);
  const annotations = useEpisodeAnnotations({
    scope,
    data: reviewTarget.data,
    canRead:
      valid &&
      capabilities.has("episode.read") &&
      capabilities.has("dataset_version.read"),
    canReadAnnotations: capabilities.has("annotation_task.read"),
    startNs,
    endNs,
  });

  const logical = useLogicalViewer(
    revision,
    query.data?.bootstrap.snapshotToken,
    capabilities.has("episode.read"),
  );
  const streams = logical.streams;
  const cameras = useMemo(
    () =>
      streams.filter(
        (stream) => stream.modality === "rgb" || stream.modality === "depth",
      ),
    [streams],
  );
  const cameraStreams = useMemo(
    () => selectCameraStreams(cameras, cameraView, clock),
    [cameras, cameraView, clock],
  );
  const jointAngleStream = useMemo(
    () => selectEpisodeJointStream(streams),
    [streams],
  );
  const { robotScene, robotSceneUnavailableReason } = useLogicalRobotScene(
    logical.robotTarget,
    jointAngleStream,
    capabilities.has("episode.read")
      ? (query.data?.episode.robotId ?? null)
      : null,
    describeEpisodeRobotPreview(query.data?.episode.robotId ?? null),
  );
  useEffect(() => {
    setCameraView("__quad__");
    setDataInfoOpen(false);
  }, [datasetId, versionId, episodeId]);

  if (!valid)
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState
          state="not-found"
          description="URL 中的 Dataset、Version 或 Episode 稳定 ID 无效。"
        />
      </main>
    );
  if (capabilities.loading)
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState state="loading" label="只读 Episode Viewer" />
      </main>
    );
  if (capabilities.failed || !capabilities.has("episode.read"))
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState
          state="forbidden"
          description="只读 Viewer 需要 episode.read。"
        />
      </main>
    );
  if (query.isPending)
    return (
      <main className={styles.page} data-page-id="P06">
        <PageState state="loading" label="只读 Episode Viewer" />
      </main>
    );
  if (query.isError)
    return (
      <main className={styles.page} data-page-id="P06">
        <RegionState
          state={datasetRegionStateForError(query.error)}
          message={
            query.error instanceof Error ? query.error.message : undefined
          }
          onRetry={() => void query.refetch()}
        />
      </main>
    );

  const fallback = routes.datasetDetail.build({
    datasetId,
    tab: "episodes",
    versionId,
  });
  const returnTo = search.returnTo ?? fallback;
  return (
    <main
      className={styles.page}
      data-page-id="P06"
      data-page-kind="episode-viewer"
      data-navigation-owner-page-id="P06"
    >
      {logical.isPending && <Alert type="info" title="正在读取固定数据快照…" />}
      {logical.error && (
        <Alert
          type="error"
          title="固定数据读取失败"
          description={logical.error.message}
          action={
            <Button onClick={() => void logical.refetch()}>重新读取</Button>
          }
        />
      )}
      {logical.data?.snapshot.streams.some((stream) => !stream.temporal) && (
        <Alert
          type="info"
          title="此快照包含独立样本"
          description="独立样本没有时间轴，不能在同步回放中显示。"
        />
      )}
      <SynchronizedEpisodeWorkbench
        adapter={{
          id: `episode-${episodeId}`,
          title: `Episode ${query.data!.episode.ordinal} · 数据查看`,
          mode: "published-readonly",
          readOnly: true,
          clock,
          cameraStreams,
          ...(robotScene
            ? { robotScene: { ...robotScene, clock } }
            : { robotSceneUnavailableReason }),
          collectionItems: [],
          findings: [],
          actions: [],
          timelineTracks: annotations.tracks,
          timelineDisabled: false,
        }}
        jointAngleStream={jointAngleStream}
        numericStreams={streams}
        jointAngleUnavailableReason="当前 Episode 未提供可读取的关节角数据。"
        slots={{
          workspaceToolbar: () => (
            <div className={styles.viewerToolbar}>
              <Button onClick={() => void navigate(returnTo)}>
                返回数据集
              </Button>
              <Typography.Text type="secondary">
                当前版本 {versionId} · 只读查看
              </Typography.Text>
            </div>
          ),
          mediaHeader: () => (
            <CameraViewToolbar
              view={cameraView}
              cameras={cameras.map((camera) => ({
                id: camera.id,
                label: camera.displayName,
              }))}
              visibleStreams={cameraStreams}
              onViewChange={setCameraView}
            >
              <Button
                size="small"
                onClick={() => {
                  setDataInfoTab("playback");
                  setDataInfoOpen(true);
                }}
              >
                数据信息
              </Button>
              {capabilities.has("upload.read") && reviewTarget.data && (
                <Button
                  size="small"
                  onClick={() => {
                    setDataInfoTab("original-files");
                    setDataInfoOpen(true);
                  }}
                >
                  原始文件
                </Button>
              )}
            </CameraViewToolbar>
          ),
          timelineTools: () => <EpisodeTagSummary annotations={annotations} />,
        }}
      />
      <Drawer
        title={reviewTarget.data ? "Episode 详情" : "数据信息"}
        size="large"
        styles={{ wrapper: { maxWidth: "100vw" } }}
        open={dataInfoOpen}
        onClose={() => setDataInfoOpen(false)}
      >
        {!reviewTarget.data && (
          <>
            <Typography.Paragraph>
              <ResourceLabel
                kind="episode"
                id={episodeId}
                datasetId={datasetId}
              />
            </Typography.Paragraph>
            <Typography.Paragraph>
              <details>
                <summary>技术信息</summary>Revision{" "}
                <code>{revision!.revision_id}</code>
              </details>
            </Typography.Paragraph>
          </>
        )}
        {reviewTarget.error && (
          <Alert type="error" title={reviewTarget.error} />
        )}
        {dataInfoOpen && reviewTarget.data && scope && (
          <EpisodeDetailPanel
            key={`${scope.organizationId}:${scope.projectId}:${scope.regionCode}:${reviewTarget.data.data_version_id}:${dataInfoTab}`}
            initialTab={dataInfoTab}
            scope={scope}
            data={reviewTarget.data}
            canRead={capabilities.has("dataset_version.read")}
            canReview={capabilities.has("dataset_version.review")}
            canDownload={capabilities.has("export.download")}
            canCreate={capabilities.has("export.create")}
            canReadOriginal={capabilities.has("upload.read")}
            returnToPlayback={() => setDataInfoOpen(false)}
          />
        )}
        {!reviewTarget.data && (
          <div className={styles.streamList}>
            {streams.map((stream) => (
              <Card key={stream.id} size="small" className={styles.streamItem}>
                <strong>{stream.displayName}</strong>
                <code>{stream.id}</code>
              </Card>
            ))}
          </div>
        )}
      </Drawer>
    </main>
  );
}

export default EpisodeViewerShell;
