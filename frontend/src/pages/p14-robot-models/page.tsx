import { useMemo, useRef, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router-dom';
import type { RobotModel } from '../../entities/robot-model';
import { useCreateRobotAssetUploadSession, usePreflightRobotModelPublish, usePublishRobotModelVersion, useRobotModels, useRobotModelVersion } from '../../features/robot-models/api';
import { RobotSceneCore } from '../../features/viewer';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { ConfirmDialog, EmptyState, ErrorPanel, PageHeader, SkeletonBlock, StandardTable, StatusBadge } from '../../shared/ui';
import { robotModelsQueryCodec } from './query-codec';
import './page.css';

const incompatibilityLabels = {
  JOINT_MAPPING: 'Joint Mapping 与模型必需关节不匹配，已阻止加载错误 3D 事实。',
  MODEL_VERSION: '模型版本与 Viewer Manifest 不匹配。',
  CALIBRATION_VERSION: '标定版本不匹配。',
  FRAME_GRAPH: 'Frame Graph 不匹配。',
} as const;

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = robotModelsQueryCodec.parse(params);
  const models = useRobotModels(search.q ? { q: search.q } : {});
  const version = useRobotModelVersion(search.versionId ?? null);
  const upload = useCreateRobotAssetUploadSession();
  const preflight = usePreflightRobotModelPublish();
  const publish = usePublishRobotModelVersion();
  const capabilities = useCapabilities();
  const [incompatibleReason, setIncompatibleReason] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishIntent, setPublishIntent] = useState<{ readonly idempotencyKey: string; readonly token: string; readonly impact: string } | null>(null);
  const publishKey = useRef<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const columns = useMemo<ColumnDef<RobotModel, unknown>[]>(() => [
    { id: 'name', header: '模型', cell: ({ row }) => <button className="link-button" type="button" onClick={() => setParams(robotModelsQueryCodec.build({ ...search, modelId: row.original.id, ...(row.original.currentPublishedVersionId ? { versionId: row.original.currentPublishedVersionId } : {}) }, search))}>{row.original.displayName}</button> },
    { id: 'manufacturer', header: '厂商', cell: ({ row }) => row.original.manufacturer },
    { id: 'modelCode', header: '型号', cell: ({ row }) => row.original.modelCode },
    { id: 'version', header: '当前已发布版本', cell: ({ row }) => row.original.currentPublishedVersionId ?? '未发布' },
  ], [search, setParams]);

  if (models.isPending) return <section className="management-page"><PageHeader title="机器人模型资产" /><SkeletonBlock width="100%" height="28rem" label="模型资产加载中" /></section>;
  if (models.error && isDomainError(models.error)) return <section className="management-page"><PageHeader title="机器人模型资产" /><ErrorPanel error={models.error} onRetry={() => void models.refetch()} /></section>;
  const items = models.data?.items ?? [];
  const selectedVersion = version.data;
  const publishAllowed = Boolean(selectedVersion && selectedVersion.lifecycle !== 'UNKNOWN' && selectedVersion.publishReadiness === 'READY' && selectedVersion.allowedActions.includes('PUBLISH') && capabilities.has('robot_model.publish'));

  const startPublishPreflight = () => {
    if (!selectedVersion?.assetManifestHash || !publishAllowed) return;
    const idempotencyKey = crypto.randomUUID();
    publishKey.current = idempotencyKey;
    preflight.mutate({
      versionId: selectedVersion.id,
      etag: selectedVersion.etag,
      expectedHash: selectedVersion.assetManifestHash,
      validationReportId: selectedVersion.validationInputHash ?? selectedVersion.id,
      changeSummary: '发布经过验证的不可变机器人模型版本',
      idempotencyKey,
    }, {
      onSuccess(result) {
        if (!result.allowed || !result.preflight_token || result.blockers.length > 0) {
          setPublishIntent(null);
          return;
        }
        setPublishIntent({
          idempotencyKey,
          token: result.preflight_token,
          impact: [...result.impacts.map((entry) => entry.message), ...result.warnings.map((entry) => entry.message)].join('；') || '发布后版本内容不可原地修改。',
        });
        setPublishOpen(true);
      },
    });
  };

  const beginUpload = async (files: FileList | null) => {
    if (!files?.length || !selectedVersion || !capabilities.has('robot_model.create')) return;
    setUploadMessage('正在计算内容 SHA-256…');
    const descriptors = await Promise.all([...files].map(async (file) => {
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
      const sha256 = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
      return { relative_path: file.webkitRelativePath || file.name, size_bytes: String(BigInt(file.size)), sha256 };
    }));
    upload.mutate({ versionId: selectedVersion.id, files: descriptors, idempotencyKey: crypto.randomUUID() }, {
      onSuccess: () => setUploadMessage('上传会话已创建；授权仅保留在内存中。'),
      onError: () => setUploadMessage('上传会话创建失败，请重试。'),
    });
  };

  return (
    <section className="management-page">
      <PageHeader title="机器人模型资产" description="模型、版本、浏览器直传、Joint Mapping 与安全 3D 预览" breadcrumbs={[{ label: '系统管理' }, { label: '机器人模型资产' }]} actions={<label className="file-button" aria-disabled={!selectedVersion || !capabilities.has('robot_model.create')}>上传模型文件<input type="file" multiple accept=".urdf,.dae,.stl,.obj,.glb" disabled={!selectedVersion || !capabilities.has('robot_model.create') || upload.isPending} onChange={(event) => void beginUpload(event.currentTarget.files)} /></label>} />
      <div className="workspace-grid">
        <section>
          <StandardTable data={items} columns={columns} getRowId={(model) => model.id} caption="机器人模型资产" empty={<EmptyState kind={search.q ? 'filtered-empty' : 'no-data'} />} />
          <p className="safe-note">浏览器直传授权仅驻留内存且 no-store。Multipart ETag 与内容 SHA-256 分栏展示，二者不等价。</p>
          {uploadMessage ? <p role="status">{uploadMessage}</p> : null}
        </section>
        <aside className="detail-panel" aria-label="模型版本详情">
          <h2>固定版本详情</h2>
          {selectedVersion ? (
            <>
              <p>ID：<code>{selectedVersion.id}</code></p>
              <p>版本：{selectedVersion.versionLabel}</p>
              <StatusBadge status={selectedVersion.lifecycle} tone={selectedVersion.lifecycle === 'UNKNOWN' ? 'warning' : selectedVersion.lifecycle === 'PUBLISHED' ? 'success' : 'neutral'} />
              <p>资源可用性：{selectedVersion.assetAvailability}</p>
              <p>发布就绪：{selectedVersion.publishReadiness}</p>
              <p>Manifest SHA-256：<code>{selectedVersion.assetManifestHash ?? '—'}</code></p>
              <div className="scene-frame">
                <RobotSceneCore
                  modelRef={{ modelId: selectedVersion.robotModelId, modelVersion: selectedVersion.id }}
                  jointMapping={{}}
                  onIncompatible={(reason) => setIncompatibleReason(incompatibilityLabels[reason])}
                />
              </div>
              {incompatibleReason ? <p role="alert">{incompatibleReason}</p> : null}
              {selectedVersion.blockedReasons.map((reason) => <p key={reason.code}>{reason.code}：{reason.message}</p>)}
              <button type="button" disabled={!publishAllowed || preflight.isPending} onClick={startPublishPreflight}>{preflight.isPending ? '正在预检…' : '预检并发布固定版本'}</button>
              {preflight.data && !preflight.data.allowed ? <p role="alert">{preflight.data.blockers.map((reason) => reason.message).join('；') || '发布预检未通过。'}</p> : null}
            </>
          ) : <EmptyState kind="no-data" title="选择固定版本" description="URL 只接受稳定 versionId，不会自动选择 latest/current。" />}
        </aside>
      </div>
      <ConfirmDialog
        open={publishOpen}
        title="发布不可变模型版本"
        resourceId={selectedVersion?.id ?? 'unknown'}
        impact={`${publishIntent?.impact ?? '缺少有效预检证据。'}${selectedVersion?.blockedReasons.map((reason) => `；${reason.message}`).join('') ?? ''}`}
        confirmLabel="确认发布"
        pending={publish.isPending}
        onCancel={() => setPublishOpen(false)}
        onConfirm={() => {
          if (!selectedVersion || !publishIntent || publishKey.current !== publishIntent.idempotencyKey) return;
          publish.mutate({ versionId: selectedVersion.id, etag: selectedVersion.etag, idempotencyKey: publishIntent.idempotencyKey, preflightToken: publishIntent.token }, { onSettled: () => { setPublishOpen(false); setPublishIntent(null); publishKey.current = null; } });
        }}
      />
    </section>
  );
}

export default Component;
