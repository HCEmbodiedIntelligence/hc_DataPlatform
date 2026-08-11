import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { isDatasetId, type DatasetId } from '../../entities/dataset';
import { isDatasetVersionId, type DatasetVersionId } from '../../entities/dataset-version';
import { isEpisodeId, type EpisodeId } from '../../entities/episode';
import {
  EpisodeWorkbenchCore,
  createPlaybackClock,
  type StreamDescriptor,
  type ViewerStreamModality,
} from '../../features/viewer';
import {
  createManualIssueCommand,
  routes as cleaningRoutes,
} from '../../features/cleaning/routing';
import { useViewerEpisodeQuery } from '../../features/datasets/api';
import { ConfirmDialog } from '../../features/datasets/components/ConfirmDialog';
import { RegionState } from '../../features/datasets/components/RegionState';
import { datasetRegionStateForError } from '../../features/datasets/components/error-state';
import { routes } from '../../features/datasets/routing';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import { assetEpisodeViewerQueryCodec } from './query-codec';
import '../../features/datasets/components/datasets.css';

const invalidDataset = 'dataset_invalid' as DatasetId;
const invalidVersion = 'version_invalid' as DatasetVersionId;
const invalidEpisode = 'episode_invalid' as EpisodeId;

function newIntentKey(prefix: string): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function decimalSecondsToNs(value: string): string | null {
  if (!/^(0|[1-9][0-9]*)(\.[0-9]{1,9})?$/.test(value)) return null;
  const [seconds = '0', fraction = ''] = value.split('.');
  return (BigInt(seconds) * 1_000_000_000n + BigInt(fraction.padEnd(9, '0') || '0')).toString();
}

function modality(kind: string): ViewerStreamModality {
  const byContractKind: Readonly<Record<string, ViewerStreamModality>> = {
    VIDEO: 'rgb',
    RGB: 'rgb',
    RGB_VIDEO: 'rgb',
    DEPTH: 'depth',
    POINTCLOUD: 'pointcloud',
    JOINT_STATE: 'joint_state',
    ACTION: 'action',
    FORCE: 'force',
    POSE: 'pose',
    IMU: 'imu',
    TACTILE: 'tactile',
    EVENT: 'event',
  };
  return byContractKind[kind.trim().toUpperCase()] ?? 'other';
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
    isDatasetId(raw.datasetId) && isDatasetVersionId(raw.versionId) && isEpisodeId(raw.episodeId);
  const datasetId = valid ? (raw.datasetId as DatasetId) : invalidDataset;
  const versionId = valid ? (raw.versionId as DatasetVersionId) : invalidVersion;
  const episodeId = valid ? (raw.episodeId as EpisodeId) : invalidEpisode;
  const query = useViewerEpisodeQuery(
    datasetId,
    versionId,
    episodeId,
    valid && capabilities.has('episode.read'),
  );
  const [selection, setSelection] = useState<{ start: string; end: string } | null>(() =>
    search.selectionStartNs && search.selectionEndNs
      ? { start: search.selectionStartNs, end: search.selectionEndNs }
      : null,
  );
  const [issueOpen, setIssueOpen] = useState(false);
  const [issueIntentKey, setIssueIntentKey] = useState('');
  const [issueType, setIssueType] = useState<
    | 'POSE_JITTER'
    | 'TIMESTAMP_DRIFT'
    | 'MISSING_FRAME'
    | 'STREAM_GAP'
    | 'CALIBRATION_MISMATCH'
    | 'INVALID_MASK'
    | 'OTHER'
  >('OTHER');
  const [issueSeverity, setIssueSeverity] = useState<'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'>(
    'MEDIUM',
  );
  const [issueNote, setIssueNote] = useState('');
  const [selectedStreamId, setSelectedStreamId] = useState<string>(search.streamId ?? '');
  const [createdIssueId, setCreatedIssueId] = useState<string | null>(null);

  const revision = query.data?.revision;
  const startNs = revision?.started_at_ns ?? '0';
  const endNs = revision
    ? (BigInt(revision.started_at_ns) + BigInt(revision.duration_ns)).toString()
    : '1';
  const clock = useMemo(
    () =>
      createPlaybackClock({
        startNs,
        endNs: BigInt(endNs) > BigInt(startNs) ? endNs : (BigInt(startNs) + 1n).toString(),
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
    const revisionEnd = BigInt(revision.started_at_ns) + BigInt(revision.duration_ns);
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

  const streams = useMemo<readonly StreamDescriptor[]>(
    () =>
      (revision?.streams ?? []).map((stream) => ({
        id: stream.episode_stream_id,
        canonicalPath: stream.channel_path,
        displayName: stream.channel_path,
        modality: modality(stream.kind),
        schema: { id: `hc.${stream.kind.toLowerCase()}`, version: 'contract-v1' },
        startNs: stream.t_start_ns,
        endNs: stream.t_end_ns,
        availability: 'unsupported',
      })),
    [revision],
  );
  useEffect(() => {
    if (!revision || selectedStreamId) return;
    const requested =
      search.streamId &&
      revision.streams.some((stream) => stream.episode_stream_id === search.streamId)
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
        throw new Error('ManualIssue 创建上下文不完整');
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
      <main className="dataset-page" data-page-id="P06">
        <RegionState
          state="not-found"
          message="URL 中的 Dataset、Version 或 Episode 稳定 ID 无效。"
        />
      </main>
    );
  if (capabilities.loading)
    return (
      <main className="dataset-page">
        <RegionState state="first-loading" />
      </main>
    );
  if (capabilities.failed || !capabilities.has('episode.read'))
    return (
      <main className="dataset-page">
        <RegionState state="forbidden" message="只读 Viewer 需要 episode.read。" />
      </main>
    );
  if (query.isPending)
    return (
      <main className="dataset-page">
        <RegionState state="first-loading" />
      </main>
    );
  if (query.isError)
    return (
      <main className="dataset-page">
        <RegionState
          state={datasetRegionStateForError(query.error)}
          message={query.error instanceof Error ? query.error.message : undefined}
          onRetry={() => void query.refetch()}
        />
      </main>
    );

  const fallback = routes.datasetDetail.build({ datasetId, tab: 'episodes', versionId });
  const returnTo = search.returnTo ?? fallback;
  const viewerReturn = `${location.pathname}${location.search}`;
  const issueAction = query.data.bootstrap.allowedActions.find(
    (action) => action.action === 'CREATE_ISSUE',
  );
  const canCreateIssue = capabilities.has('manual_issue.create') && issueAction?.allowed === true;
  const issueBlockedReasons = [
    ...(!issueAction?.allowed
      ? (issueAction?.blockedReasons.map((reason) => reason.message) ?? [
          '资源 allowed_actions 未允许 CREATE_ISSUE',
        ])
      : []),
    ...(!selection ? ['请先在时间轴选择有效半开区间 [start,end)'] : []),
    ...(!selectedStreamId ? ['请选择固定 Revision 中的 Episode Stream'] : []),
    ...(!scope?.organizationId || !scope.projectId || !scope.regionCode
      ? ['当前 Project/Region Scope 不可用']
      : []),
  ];
  return (
    <main
      className="dataset-page"
      data-page-id="P06"
      data-page-kind="episode-viewer"
      data-navigation-owner-page-id="P06"
    >
      <header className="dataset-resource-header">
        <div>
          <p className="dataset-eyebrow">Readonly episode viewer</p>
          <h1>Episode {episodeId}</h1>
          <p>
            <code>{datasetId}</code> · <code>{versionId}</code> · Revision{' '}
            <code>{revision!.revision_id}</code>
          </p>
        </div>
        <div className="dataset-actions">
          <button
            type="button"
            className="dataset-button dataset-button--secondary"
            onClick={() => {
              void navigate(returnTo);
            }}
          >
            返回
          </button>
        </div>
      </header>
      <section className="dataset-workbench-shell">
        <aside className="dataset-workbench-rail">
          <h2>Streams</h2>
          {streams.map((stream) => (
            <div key={stream.id}>
              <strong>{stream.displayName}</strong>
              <small>{stream.id}</small>
            </div>
          ))}
        </aside>
        <div className="dataset-workbench-core">
          <EpisodeWorkbenchCore
            episodeId={episodeId}
            datasetId={datasetId}
            versionId={versionId}
            clock={clock}
            streams={streams}
            mode="readonly"
            onTimeRangeSelect={(start, end) => setSelection({ start, end })}
          />
        </div>
        <aside className="dataset-workbench-inspector">
          <h2>交接</h2>
          <p>Viewer 保持只读，时间范围采用 [start, end)。</p>
          {selection ? (
            <p>
              <code>{selection.start}</code>
              <br />—<br />
              <code>{selection.end}</code>
            </p>
          ) : (
            <p>在时间轴拖动选择范围。</p>
          )}
          {revision!.streams.length > 1 ? (
            <label>
              Stream
              <select
                value={selectedStreamId}
                onChange={(event) => setSelectedStreamId(event.target.value)}
              >
                <option value="">请选择</option>
                {revision!.streams.map((stream) => (
                  <option key={stream.episode_stream_id} value={stream.episode_stream_id}>
                    {stream.channel_path}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {canCreateIssue ? (
            <button
              type="button"
              className="dataset-button"
              onClick={() => {
                setIssueIntentKey(newIntentKey('viewer-manual-issue'));
                setIssueOpen(true);
              }}
            >
              添加人工问题
            </button>
          ) : null}
          {capabilities.has('manual_issue.read') ? (
            <button
              type="button"
              className="dataset-button dataset-button--secondary"
              onClick={() => {
                void navigate(
                  cleaningRoutes.manualIssues.build({
                    datasetId,
                    versionId,
                    episodeId,
                    ...(createdIssueId ? { issueId: createdIssueId } : {}),
                    returnTo: viewerReturn,
                  }),
                );
              }}
            >
              查看问题清单
            </button>
          ) : null}
          {createdIssueId ? (
            <p role="status">
              问题已添加：<code>{createdIssueId}</code>
            </p>
          ) : null}
          {!canCreateIssue ? (
            <RegionState
              state="feature-unavailable"
              message="ManualIssue 创建需要 capability 与资源 CREATE_ISSUE 同时允许；不会创建 CleaningDraft，也不会直达 P11。"
            />
          ) : null}
        </aside>
      </section>
      <ConfirmDialog
        open={issueOpen}
        title="添加人工问题"
        resourceId={`${revision!.revision_id}:${selectedStreamId || 'stream-unselected'}`}
        description="只创建 P09 Owner 的 ManualIssue，并保留当前 Viewer 时间点；不会创建 CleaningDraft。"
        impact={[
          `固定 Version ${versionId}`,
          `固定 Episode / Revision ${episodeId} / ${revision!.revision_id}`,
          selection ? `范围 [${selection.start}, ${selection.end})` : '尚未选择时间范围',
        ]}
        blockedReasons={issueBlockedReasons}
        confirmLabel="确认添加问题"
        confirmDisabled={
          issueBlockedReasons.length > 0 || !issueNote.trim() || issueMutation.isPending
        }
        submitting={issueMutation.isPending}
        onConfirm={() =>
          issueMutation.mutate(undefined, {
            onSuccess: (issue) => {
              setCreatedIssueId(issue.id);
              setIssueOpen(false);
              setIssueNote('');
            },
          })
        }
        onCancel={() => setIssueOpen(false)}
      >
        <label>
          问题类型
          <select
            value={issueType}
            onChange={(event) => setIssueType(event.target.value as typeof issueType)}
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
            onChange={(event) => setIssueSeverity(event.target.value as typeof issueSeverity)}
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
            {issueMutation.error instanceof Error ? issueMutation.error.message : '问题添加失败'}
          </p>
        ) : null}
      </ConfirmDialog>
    </main>
  );
}

export default EpisodeViewerShell;
