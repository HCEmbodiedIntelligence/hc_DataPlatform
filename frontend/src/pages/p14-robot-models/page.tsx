import { Button, Input, Select } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { Box, Boxes, FileStack, Search, ShieldCheck, Upload } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { RobotModel } from '../../entities/robot-model';
import {
  useCreateRobotAssetUploadSession,
  usePreflightRobotModelPublish,
  usePublishRobotModelVersion,
  useRobotModels,
  useRobotModelVersion,
} from '../../features/robot-models/api';
import { RobotSceneCore } from '../../features/viewer';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  DangerConfirmModal,
  DataCursorPager,
  DataTable,
  DetailTabs,
  FilterToolbar,
  PageState,
  SecureUploadPicker,
  StandardPageScaffold,
  StatusTag,
} from '../../shared/ui';
import workspace from '../ui-011e/workspace.module.css';
import { robotModelsQueryCodec } from './query-codec';

const detailTabs = [
  { id: 'overview', label: '概览' },
  { id: 'assets', label: '资产文件' },
  { id: 'mapping', label: '关节映射' },
  { id: 'bindings', label: '绑定机器人' },
  { id: 'validations', label: '校验记录' },
] as const;

const incompatibilityLabels = {
  JOINT_MAPPING: 'Joint Mapping 与模型必需关节不匹配，已阻止加载错误 3D 事实。',
  MODEL_VERSION: '模型版本与 Viewer Manifest 不匹配。',
  CALIBRATION_VERSION: '标定版本不匹配。',
  FRAME_GRAPH: 'Frame Graph 不匹配。',
} as const;

interface PublishIntent {
  readonly idempotencyKey: string;
  readonly token: string;
  readonly impact: string;
  readonly preparedAt: string;
  readonly expiresAt: string;
  readonly resourceVersion: string;
}

function SummaryItem({
  icon,
  label,
  value,
}: Readonly<{ icon: React.ReactNode; label: string; value: string }>) {
  return (
    <section className={workspace.summaryItem} aria-label={label}>
      <span className={workspace.summaryIcon}>{icon}</span>
      <span className={workspace.summaryCopy}>
        <span>{label}</span>
        <strong>{value}</strong>
      </span>
    </section>
  );
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = robotModelsQueryCodec.parse(params);
  const [query, setQuery] = useState(search.q ?? '');
  const [files, setFiles] = useState<readonly File[]>([]);
  const models = useRobotModels(search.q ? { q: search.q } : {});
  const version = useRobotModelVersion(search.versionId ?? null);
  const upload = useCreateRobotAssetUploadSession();
  const preflight = usePreflightRobotModelPublish();
  const publish = usePublishRobotModelVersion();
  const capabilities = useCapabilities();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const [incompatibleReason, setIncompatibleReason] = useState<string | null>(null);
  const [publishIntent, setPublishIntent] = useState<PublishIntent | null>(null);
  const publishKey = useRef<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const items = models.data?.items ?? [];
  const selectedModel = items.find((item) => item.id === search.modelId) ?? items[0];
  const selectedVersion = version.data;
  const publishAllowed = Boolean(
    selectedVersion &&
      selectedVersion.lifecycle !== 'UNKNOWN' &&
      selectedVersion.publishReadiness === 'READY' &&
      selectedVersion.allowedActions.includes('PUBLISH') &&
      capabilities.has('robot_model.publish'),
  );

  const columns = useMemo<ColumnDef<RobotModel, unknown>[]>(
    () => [
      {
        id: 'name',
        header: '模型',
        size: 170,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() =>
              setParams(
                robotModelsQueryCodec.build(
                  {
                    ...search,
                    modelId: row.original.id,
                    ...(row.original.currentPublishedVersionId
                      ? { versionId: row.original.currentPublishedVersionId }
                      : {}),
                  },
                  search,
                ),
              )
            }
          >
            {row.original.displayName}
          </Button>
        ),
      },
      {
        id: 'manufacturer',
        header: '厂商',
        size: 120,
        cell: ({ row }) => row.original.manufacturer,
      },
      { id: 'modelCode', header: '型号', size: 110, cell: ({ row }) => row.original.modelCode },
      {
        id: 'version',
        header: '当前版本',
        size: 180,
        cell: ({ row }) => row.original.currentPublishedVersionId ?? '未发布',
      },
      {
        id: 'binding',
        header: '绑定状态',
        size: 100,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.currentPublishedVersionId ? 'BOUND' : 'UNBOUND'}
            label={row.original.currentPublishedVersionId ? '已发布' : '未发布'}
            tone={row.original.currentPublishedVersionId ? 'success' : 'neutral'}
          />
        ),
      },
    ],
    [search, setParams],
  );

  const startPublishPreflight = () => {
    if (!selectedVersion?.assetManifestHash || !publishAllowed) return;
    const idempotencyKey = crypto.randomUUID();
    publishKey.current = idempotencyKey;
    preflight.mutate(
      {
        versionId: selectedVersion.id,
        etag: selectedVersion.etag,
        expectedHash: selectedVersion.assetManifestHash,
        validationReportId: selectedVersion.validationInputHash ?? selectedVersion.id,
        changeSummary: '发布经过验证的不可变机器人模型版本',
        idempotencyKey,
      },
      {
        onSuccess(result) {
          if (
            !result.allowed ||
            !result.preflight_token ||
            !result.expires_at ||
            result.blockers.length > 0
          ) {
            setPublishIntent(null);
            return;
          }
          setPublishIntent({
            idempotencyKey,
            token: result.preflight_token,
            impact:
              [...result.impacts, ...result.warnings].map((entry) => entry.message).join('；') ||
              '发布后版本内容不可原地修改。',
            preparedAt: new Date().toISOString(),
            expiresAt: result.expires_at,
            resourceVersion: result.resource_revision,
          });
        },
      },
    );
  };

  const beginUpload = async (nextFiles: readonly File[]) => {
    setFiles(nextFiles);
    if (!nextFiles.length || !selectedVersion || !capabilities.has('robot_model.create')) return;
    setUploadMessage('正在计算内容 SHA-256…');
    const descriptors = await Promise.all(
      nextFiles.map(async (file) => {
        const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        const sha256 = [...new Uint8Array(digest)]
          .map((byte) => byte.toString(16).padStart(2, '0'))
          .join('');
        return {
          relative_path: file.webkitRelativePath || file.name,
          size_bytes: String(BigInt(file.size)),
          sha256,
        };
      }),
    );
    upload.mutate(
      { versionId: selectedVersion.id, files: descriptors, idempotencyKey: crypto.randomUUID() },
      {
        onSuccess: () => setUploadMessage('上传会话已创建；授权仅保留在内存中。'),
        onError: () => setUploadMessage('上传会话创建失败，请重试。'),
      },
    );
  };

  const pageState = models.isPending ? (
    <PageState state="loading" label="机器人模型资产" />
  ) : models.error && isDomainError(models.error) ? (
    <PageState
      state={models.error.httpStatus === 403 ? 'forbidden' : 'error'}
      onRetry={() => void models.refetch()}
    />
  ) : null;

  return (
    <main className={workspace.page}>
      <StandardPageScaffold
        header={{
          title: '机器人模型资产',
          description: '管理一次性上传的 URDF 与 Mesh，并将固定版本安全绑定到机器人。',
          breadcrumbs: [
            { key: 'settings', label: '系统管理', to: '/settings/robot-models' },
            { key: 'models', label: '机器人模型资产' },
          ],
          actions: (
            <>
              <SecureUploadPicker
                files={files}
                multiple
                maxCount={20}
                accept=".urdf,.dae,.stl,.obj,.glb"
                label="选择模型文件"
                disabled={
                  !selectedVersion || !capabilities.has('robot_model.create') || upload.isPending
                }
                onFilesChange={(nextFiles) => void beginUpload(nextFiles)}
              />
              <Button icon={<Upload aria-hidden="true" size={15} />} disabled>
                新建模型版本
              </Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem icon={<Boxes size={20} />} label="模型总数" value={String(items.length)} />
            <SummaryItem
              icon={<FileStack size={20} />}
              label="已发布版本"
              value={String(items.filter((item) => item.currentPublishedVersionId).length)}
            />
            <SummaryItem icon={<ShieldCheck size={20} />} label="安全模式" value="固定 ID" />
            <SummaryItem icon={<Box size={20} />} label="资产传输" value="浏览器直传" />
          </div>
        }
        filters={
          <FilterToolbar
            onApply={() =>
              setParams(
                robotModelsQueryCodec.build(
                  { ...search, q: query || undefined, after: undefined, before: undefined },
                  search,
                ),
              )
            }
            onReset={() => {
              setQuery('');
              setParams(
                robotModelsQueryCodec.build(
                  { ...search, q: undefined, binding: 'all', after: undefined, before: undefined },
                  search,
                ),
              );
            }}
          >
            <label className={workspace.toolbarField}>
              <span>模型名称</span>
              <Input.Search
                className={workspace.toolbarSearch}
                value={query}
                placeholder="搜索模型名称"
                enterButton={
                  <Button
                    aria-label="搜索模型名称"
                    icon={<Search aria-hidden="true" size={15} />}
                  />
                }
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>绑定状态</span>
              <Select
                value={search.binding}
                options={[
                  { value: 'all', label: '全部' },
                  { value: 'bound', label: '已绑定' },
                  { value: 'unbound', label: '未绑定' },
                ]}
                onChange={(binding) =>
                  setParams(
                    robotModelsQueryCodec.build(
                      { ...search, binding, after: undefined, before: undefined },
                      search,
                    ),
                  )
                }
              />
            </label>
          </FilterToolbar>
        }
        state={pageState}
      >
        <div className={workspace.twoPane}>
          <section className={workspace.pane} aria-label="机器人模型列表">
            <header className={workspace.paneHeader}>
              <div>
                <h2>模型资产</h2>
                <p>列表只展示授权范围内的稳定资源</p>
              </div>
              <span className={workspace.inlineMeta}>共 {items.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={items}
                columns={columns}
                getRowId={(model) => model.id}
                caption="机器人模型资产"
                state={items.length ? 'ready' : 'empty'}
                empty={<PageState state={search.q ? 'filtered-empty' : 'empty'} />}
              />
            </div>
            {models.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
                  pageInfo={{
                    startCursor: models.data.pageInfo.start_cursor,
                    endCursor: models.data.pageInfo.end_cursor,
                    hasPreviousPage: models.data.pageInfo.has_previous_page,
                    hasNextPage: models.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(robotModelsQueryCodec.build({ ...search, ...cursor }, search))
                  }
                  windowLabel={`当前 ${items.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <aside className={workspace.inspector} aria-label="模型版本详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>{selectedModel?.displayName ?? '固定版本详情'}</h2>
                <p>
                  {selectedVersion ? selectedVersion.versionLabel : '列表事实 / 未加载固定版本'}
                </p>
              </div>
              <StatusTag
                status={selectedVersion?.lifecycle ?? (selectedModel ? 'LISTED' : 'UNKNOWN')}
                label={selectedVersion?.lifecycle ?? (selectedModel ? '已收录' : '未知状态')}
                tone={
                  selectedVersion?.lifecycle === 'PUBLISHED' || selectedModel
                    ? 'success'
                    : 'warning'
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <div className={workspace.visualStage}>
                {selectedVersion ? (
                  <RobotSceneCore
                    modelRef={{
                      modelId: selectedVersion.robotModelId,
                      modelVersion: selectedVersion.id,
                    }}
                    jointMapping={{}}
                    onIncompatible={(reason) =>
                      setIncompatibleReason(incompatibilityLabels[reason])
                    }
                  />
                ) : (
                  <div className={workspace.visualStageCopy}>
                    <Box aria-hidden="true" size={48} />
                    <strong>{selectedModel?.modelCode ?? '选择固定模型版本'}</strong>
                    <span>
                      3D 资源只在 URL 携带稳定 versionId 且合同通过后加载，不自动回退到
                      latest/current。
                    </span>
                  </div>
                )}
              </div>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>模型 ID</dt>
                  <dd>
                    <code>{selectedModel?.id ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>厂商 / 型号</dt>
                  <dd>
                    {selectedModel
                      ? `${selectedModel.manufacturer} / ${selectedModel.modelCode}`
                      : '—'}
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>固定版本</dt>
                  <dd>
                    <code>
                      {selectedVersion?.id ?? selectedModel?.currentPublishedVersionId ?? '未发布'}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>资源可用性</dt>
                  <dd>{selectedVersion?.assetAvailability ?? '需加载固定版本'}</dd>
                </div>
              </dl>
              <DetailTabs
                tabs={detailTabs}
                activeTab={search.detailTab}
                onChange={(detailTab) =>
                  setParams(
                    robotModelsQueryCodec.build(
                      { ...search, detailTab: detailTab as typeof search.detailTab },
                      search,
                    ),
                  )
                }
              />
              <section className={workspace.tabContent} role="tabpanel">
                {search.detailTab === 'overview' ? (
                  <p className={workspace.safeNote}>
                    浏览器直传授权仅驻留内存且 no-store；Multipart ETag 与内容 SHA-256 不等价。
                  </p>
                ) : null}
                {search.detailTab === 'assets' ? (
                  <PageState
                    state="feature-unavailable"
                    title="固定版本资产清单未加载"
                    description="当前列表 DTO 不包含资产对象，不用视觉占位伪造文件。"
                  />
                ) : null}
                {search.detailTab === 'mapping' ? (
                  <PageState
                    state="feature-unavailable"
                    title="关节映射需固定版本合同"
                    description="映射未授权时保持只读，不生成虚构关节。"
                  />
                ) : null}
                {search.detailTab === 'bindings' ? (
                  <p className={workspace.featureNote}>
                    绑定机器人数量不在列表 DTO 中；仅显示已发布版本 ID。
                  </p>
                ) : null}
                {search.detailTab === 'validations' ? (
                  <p className={workspace.featureNote}>
                    校验报告需固定 versionId；当前不伪造通过记录。
                  </p>
                ) : null}
              </section>
              {incompatibleReason ? (
                <p className={workspace.warningNote} role="alert">
                  {incompatibleReason}
                </p>
              ) : null}
              {uploadMessage ? (
                <p className={workspace.safeNote} role="status">
                  {uploadMessage}
                </p>
              ) : null}
              <div className={workspace.actionRow}>
                <Button
                  type="primary"
                  disabled={!publishAllowed || preflight.isPending}
                  loading={preflight.isPending}
                  onClick={startPublishPreflight}
                >
                  重新验证并发布
                </Button>
                <Button disabled>绑定机器人</Button>
              </div>
            </div>
          </aside>
        </div>
      </StandardPageScaffold>

      <DangerConfirmModal
        open={publishIntent !== null}
        title="发布不可变模型版本"
        actionLabel="确认发布"
        resourceId={selectedVersion?.id ?? ''}
        impact={publishIntent?.impact ?? '缺少有效预检证据。'}
        blockers={selectedVersion?.blockedReasons ?? []}
        preflight={
          publishIntent
            ? {
                preparedAt: publishIntent.preparedAt,
                expiresAt: publishIntent.expiresAt,
                resourceVersion: publishIntent.resourceVersion,
                scopeKey,
              }
            : null
        }
        currentScopeKey={scopeKey}
        pending={publish.isPending}
        onCancel={() => {
          setPublishIntent(null);
          publishKey.current = null;
        }}
        onConfirm={() => {
          if (
            !selectedVersion ||
            !publishIntent ||
            publishKey.current !== publishIntent.idempotencyKey
          )
            return;
          publish.mutate(
            {
              versionId: selectedVersion.id,
              etag: selectedVersion.etag,
              idempotencyKey: publishIntent.idempotencyKey,
              preflightToken: publishIntent.token,
            },
            {
              onSettled: () => {
                setPublishIntent(null);
                publishKey.current = null;
              },
            },
          );
        }}
      />
    </main>
  );
}

export default Component;
