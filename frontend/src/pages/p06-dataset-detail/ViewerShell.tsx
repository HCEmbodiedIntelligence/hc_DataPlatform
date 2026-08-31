import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Modal, Typography } from "antd";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { isDatasetId, type DatasetId } from "../../entities/dataset";
import {
  isDatasetVersionId,
  type DatasetVersionId,
} from "../../entities/dataset-version";
import { isEpisodeId, type EpisodeId } from "../../entities/episode";
import {
  EpisodeWorkbenchCore,
  createPlaybackClock,
} from "../../features/viewer";
import {
  createManualIssueCommand,
  routes as cleaningRoutes,
} from "../../features/cleaning/routing";
import { useViewerEpisodeQuery } from "../../features/datasets/api";
import { RegionState } from "../../features/datasets/components/RegionState";
import { datasetRegionStateForError } from "../../features/datasets/components/error-state";
import { routes } from "../../features/datasets/routing";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState, WorkbenchScaffold } from "../../shared/ui";
import { assetEpisodeViewerQueryCodec } from "./query-codec";
import { adaptP06ViewerStreams } from "./viewer-stream-adapter";
import styles from "./styles.module.css";

const invalidDataset = "dataset_invalid" as DatasetId;
const invalidVersion = "version_invalid" as DatasetVersionId;
const invalidEpisode = "episode_invalid" as EpisodeId;

function newIntentKey(prefix: string): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

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
  const location = useLocation();
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
  const [selection, setSelection] = useState<{
    start: string;
    end: string;
  } | null>(() =>
    search.selectionStartNs && search.selectionEndNs
      ? { start: search.selectionStartNs, end: search.selectionEndNs }
      : null,
  );
  const [issueOpen, setIssueOpen] = useState(false);
  const [issueIntentKey, setIssueIntentKey] = useState("");
  const [issueType, setIssueType] = useState<
    | "POSE_JITTER"
    | "TIMESTAMP_DRIFT"
    | "MISSING_FRAME"
    | "STREAM_GAP"
    | "CALIBRATION_MISMATCH"
    | "INVALID_MASK"
    | "OTHER"
  >("OTHER");
  const [issueSeverity, setIssueSeverity] = useState<
    "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  >("MEDIUM");
  const [issueNote, setIssueNote] = useState("");
  const [selectedStreamId, setSelectedStreamId] = useState<string>(
    search.streamId ?? "",
  );
  const [createdIssueId, setCreatedIssueId] = useState<string | null>(null);

  const revision = query.data?.revision;
  const startNs = revision?.started_at_ns ?? "0";
  const endNs = revision
    ? (BigInt(revision.started_at_ns) + BigInt(revision.duration_ns)).toString()
    : "1";
  const clock = useMemo(
    () =>
      createPlaybackClock({
        startNs,
        endNs:
          BigInt(endNs) > BigInt(startNs)
            ? endNs
            : (BigInt(startNs) + 1n).toString(),
      }),
    [endNs, startNs],
  );
  useEffect(() => () => clock.dispose(), [clock]);
  useEffect(() => {
    if (!search.t) return;
    const value = decimalSecondsToNs(search.t);
    if (value !== null) clock.seek(value);
  }, [clock, search.t]);
  useEffect(() => {
    if (!revision || !selection) return;
    const revisionEnd =
      BigInt(revision.started_at_ns) + BigInt(revision.duration_ns);
    if (
      !/^\d+$/.test(selection.start) ||
      !/^\d+$/.test(selection.end) ||
      BigInt(selection.start) < BigInt(revision.started_at_ns) ||
      BigInt(selection.end) > revisionEnd ||
      BigInt(selection.start) >= BigInt(selection.end)
    ) {
      setSelection(null);
    }
  }, [revision, selection]);

  const streams = useMemo(
    () => (revision ? adaptP06ViewerStreams(revision, datasetId) : []),
    [datasetId, revision],
  );
  useEffect(() => {
    if (!revision || selectedStreamId) return;
    const requested =
      search.streamId &&
      revision.streams.some(
        (stream) => stream.episode_stream_id === search.streamId,
      )
        ? search.streamId
        : revision.streams.length === 1
          ? revision.streams[0]?.episode_stream_id
          : undefined;
    if (requested) setSelectedStreamId(requested);
  }, [revision, search.streamId, selectedStreamId]);

  const issueMutation = useMutation({
    mutationFn: async () => {
      if (
        !scope?.organizationId ||
        !scope.projectId ||
        !scope.regionCode ||
        !revision ||
        !selection ||
        !selectedStreamId ||
        !issueIntentKey
      ) {
        throw new Error("ManualIssue 创建上下文不完整");
      }
      return createManualIssueCommand({
        organizationId: scope.organizationId,
        projectId: scope.projectId,
        regionCode: scope.regionCode,
        datasetId,
        versionId,
        episodeId,
        revisionId: revision.revision_id,
        streamId: selectedStreamId,
        startNs: selection.start,
        endNs: selection.end,
        issueType,
        severity: issueSeverity,
        note: issueNote.trim(),
        idempotencyKey: issueIntentKey,
      });
    },
  });

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
  const viewerReturn = `${location.pathname}${location.search}`;
  const issueAction = query.data.bootstrap.allowedActions.find(
    (action) => action.action === "CREATE_ISSUE",
  );
  const canCreateIssue =
    capabilities.has("manual_issue.create") && issueAction?.allowed === true;
  const issueBlockedReasons = [
    ...(!issueAction?.allowed
      ? (issueAction?.blockedReasons.map((reason) => reason.message) ?? [
          "资源 allowed_actions 未允许 CREATE_ISSUE",
        ])
      : []),
    ...(!selection ? ["请先在时间轴选择有效半开区间 [start,end)"] : []),
    ...(!selectedStreamId ? ["请选择固定 Revision 中的 Episode Stream"] : []),
    ...(!scope?.organizationId || !scope.projectId || !scope.regionCode
      ? ["当前 Project/Region Scope 不可用"]
      : []),
  ];
  return (
    <main
      className={styles.page}
      data-page-id="P06"
      data-page-kind="episode-viewer"
      data-navigation-owner-page-id="P06"
    >
      <WorkbenchScaffold
        header={{
          title: `Episode ${episodeId}`,
          breadcrumbs: [
            {
              key: datasetId,
              label: <code>{datasetId}</code>,
              to: routes.datasetDetail.build({ datasetId }),
            },
            {
              key: versionId,
              label: <code>{versionId}</code>,
              to: routes.versionDetail.build({ datasetId, versionId }),
            },
            { key: episodeId, label: <code>{episodeId}</code> },
          ],
          metadata: (
            <>
              Revision <code>{revision!.revision_id}</code>
            </>
          ),
          actions: (
            <Button onClick={() => void navigate(returnTo)}>返回</Button>
          ),
        }}
        navigation={
          <div className={styles.streamList}>
            <Typography.Title level={2}>Streams</Typography.Title>
            {streams.map((stream) => (
              <Card key={stream.id} size="small" className={styles.streamItem}>
                <strong>{stream.displayName}</strong>
                <code>{stream.id}</code>
              </Card>
            ))}
          </div>
        }
        media={
          <EpisodeWorkbenchCore
            episodeId={episodeId}
            datasetId={datasetId}
            versionId={versionId}
            clock={clock}
            streams={streams}
            mode="readonly"
            onTimeRangeSelect={(start, end) => setSelection({ start, end })}
          />
        }
        editor={
          <Card title="只读约束" size="small">
            <Typography.Paragraph>
              媒体、时间轴与通道数据为只读。
            </Typography.Paragraph>
          </Card>
        }
        inspector={
          <div className={styles.viewerInspector}>
            <Typography.Title level={2}>交接</Typography.Title>
            {selection ? (
              <Typography.Paragraph>
                <code>{selection.start}</code>
                <br />—<br />
                <code>{selection.end}</code>
              </Typography.Paragraph>
            ) : (
              <Typography.Paragraph>
                在时间轴拖动选择范围。
              </Typography.Paragraph>
            )}
            {revision!.streams.length > 1 ? (
              <label className={styles.filterField}>
                Stream
                <select
                  value={selectedStreamId}
                  onChange={(event) => setSelectedStreamId(event.target.value)}
                >
                  <option value="">请选择</option>
                  {revision!.streams.map((stream) => (
                    <option
                      key={stream.episode_stream_id}
                      value={stream.episode_stream_id}
                    >
                      {stream.channel_path}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {canCreateIssue ? (
              <Button
                type="primary"
                onClick={() => {
                  setIssueIntentKey(newIntentKey("viewer-manual-issue"));
                  setIssueOpen(true);
                }}
              >
                添加人工问题
              </Button>
            ) : null}
            {capabilities.has("manual_issue.read") ? (
              <Button
                onClick={() =>
                  void navigate(
                    cleaningRoutes.manualIssues.build({
                      datasetId,
                      versionId,
                      episodeId,
                      ...(createdIssueId ? { issueId: createdIssueId } : {}),
                      returnTo: viewerReturn,
                    }),
                  )
                }
              >
                查看问题清单
              </Button>
            ) : null}
            {createdIssueId ? (
              <Alert
                type="success"
                showIcon
                title="问题已添加"
                description={<code>{createdIssueId}</code>}
              />
            ) : null}
            {!canCreateIssue ? (
              <PageState
                state="feature-unavailable"
                description="ManualIssue 创建需要 capability 与资源 CREATE_ISSUE 同时允许；不会创建 CleaningDraft，也不会直达 P11。"
              />
            ) : null}
          </div>
        }
      />
      <Modal
        open={issueOpen}
        title="添加人工问题"
        closable={!issueMutation.isPending}
        mask={{ closable: false }}
        onCancel={() => {
          if (!issueMutation.isPending) setIssueOpen(false);
        }}
        footer={[
          <Button
            key="cancel"
            disabled={issueMutation.isPending}
            onClick={() => setIssueOpen(false)}
          >
            取消
          </Button>,
          <Button
            key="confirm"
            type="primary"
            loading={issueMutation.isPending}
            disabled={issueBlockedReasons.length > 0 || !issueNote.trim()}
            onClick={() =>
              issueMutation.mutate(undefined, {
                onSuccess: (issue) => {
                  setCreatedIssueId(issue.id);
                  setIssueOpen(false);
                  setIssueNote("");
                },
              })
            }
          >
            确认添加问题
          </Button>,
        ]}
      >
        <Typography.Paragraph>
          创建问题记录，并保留当前时间点。
        </Typography.Paragraph>
        <div className={styles.reviewForm}>
          <label>
            问题类型
            <select
              value={issueType}
              onChange={(event) =>
                setIssueType(event.target.value as typeof issueType)
              }
            >
              <option value="POSE_JITTER">姿态抖动</option>
              <option value="TIMESTAMP_DRIFT">时间漂移</option>
              <option value="MISSING_FRAME">缺帧</option>
              <option value="STREAM_GAP">流中断</option>
              <option value="CALIBRATION_MISMATCH">标定不匹配</option>
              <option value="INVALID_MASK">无效掩码</option>
              <option value="OTHER">其他</option>
            </select>
          </label>
          <label>
            严重级别
            <select
              value={issueSeverity}
              onChange={(event) =>
                setIssueSeverity(event.target.value as typeof issueSeverity)
              }
            >
              <option value="LOW">低</option>
              <option value="MEDIUM">中</option>
              <option value="HIGH">高</option>
              <option value="CRITICAL">严重</option>
            </select>
          </label>
          <label>
            说明
            <textarea
              required
              maxLength={8192}
              value={issueNote}
              onChange={(event) => setIssueNote(event.target.value)}
            />
          </label>
          {issueMutation.isError ? (
            <p role="alert">
              {issueMutation.error instanceof Error
                ? issueMutation.error.message
                : "问题添加失败"}
            </p>
          ) : null}
          {issueBlockedReasons.length ? (
            <Alert
              type="warning"
              showIcon
              title="当前不可提交"
              description={
                <ul>
                  {issueBlockedReasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              }
            />
          ) : null}
        </div>
      </Modal>
    </main>
  );
}

export default EpisodeViewerShell;
