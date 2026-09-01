import { Alert, Button, Descriptions, Flex, Input, Select, Space, Typography } from 'antd';
import { Database, HardDriveUpload, Plus, Radio, TriangleAlert, Upload } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { canMutateDataSource, isUnknownEnum } from '../../entities/data-source';
import {
  useDataSource,
  useDataSourceMutation,
  useDataSourcesPage,
  useTestDataSourceConnection,
} from '../../features/ingest/api';
import {
  isConnectorEditable,
  toWritableBindingWire,
  toWritableConnectorWire,
  type WritableConnectorBinding,
} from '../../features/ingest/connectors/registry';
import { createMutationIntentKey } from '../../features/ingest/mutation-machine';
import { routes } from '../../features/ingest/routing';
import { useIngestScope } from '../../features/ingest/use-ingest-scope';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAsyncJob } from '../../shared/jobs/use-async-job';
import { formatStorageSize } from '../../shared/lib/metric-presentation';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  CursorPager,
  EntityDrawer,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
  type DangerConflict,
  type DangerPreflightEvidence,
  type PageStateKind,
} from '../../shared/ui';
import { DataSourceTable } from './components/DataSourceTable';
import {
  ConfirmSourceStateDialog,
  ConnectorDeleteConfirmDialog,
  CredentialRotationDialog,
} from './components/SourceActionDialogs';
import { RobotIdentityConsole } from './components/RobotIdentityConsole';
import { SourceEditorDialog, type SourceDraft } from './components/SourceEditorDialog';
import {
  dataSourcesQueryCodec,
  updateDataSourcesSearch,
  type DataSourcesSearch,
} from './query-codec';
import styles from './styles.module.css';

interface FilterDraft {
  readonly q: string;
  readonly sourceType: string;
  readonly administrativeState: string;
  readonly connectivity: string;
  readonly credentialState: string;
  readonly sort: DataSourcesSearch['sort'];
  readonly limit: DataSourcesSearch['limit'];
}

function filterDraft(search: DataSourcesSearch): FilterDraft {
  return {
    q: search.q ?? '',
    sourceType: search.sourceType[0] ?? '',
    administrativeState: search.administrativeState[0] ?? '',
    connectivity: search.connectivity[0] ?? '',
    credentialState: search.credentialState[0] ?? '',
    sort: search.sort,
    limit: search.limit,
  };
}

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return 'contract-mismatch';
  switch (error.code) {
    case 'FORBIDDEN':
    case 'UNAUTHENTICATED':
      return 'forbidden';
    case 'NOT_FOUND':
      return 'not-found';
    case 'GONE':
      return 'gone';
    case 'VERSION_CONFLICT':
    case 'PRECONDITION_FAILED':
      return 'conflict';
    case 'RATE_LIMITED':
      return 'rate-limited';
    case 'NETWORK_ERROR':
      return 'offline';
    case 'CONTRACT_MISMATCH':
      return 'contract-mismatch';
    default:
      return 'error';
  }
}

function listState(
  query: {
    readonly isPending: boolean;
    readonly isError: boolean;
    readonly isFetching: boolean;
    readonly error: unknown;
    readonly data?: { readonly items: readonly unknown[] };
  },
  filtered: boolean,
): PageStateKind | 'ready' {
  if (query.isPending) return 'loading';
  if (query.isError) return stateFromError(query.error);
  if (query.data?.items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  return query.isFetching ? 'refreshing' : 'ready';
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function safeOperationError(error: unknown): string | null {
  if (!error) return null;
  if (!isDomainError(error)) return '操作未完成；服务端事实没有被乐观推进。';
  const message =
    error.code === 'VERSION_CONFLICT' || error.code === 'PRECONDITION_FAILED'
      ? '资源版本已变化，请重新加载后再提交。'
      : error.code === 'FORBIDDEN' || error.code === 'UNAUTHENTICATED'
        ? '当前授权不允许执行此操作。'
        : error.code === 'VALIDATION_ERROR'
          ? '输入未通过服务端校验，请核对后重试。'
          : '操作未完成；服务端事实没有被乐观推进。';
  return error.requestId ? `${message} 请求 ID：${error.requestId}` : message;
}

function conflictFrom(error: unknown): DangerConflict | null {
  if (!isDomainError(error)) return null;
  if (error.httpStatus === 409 || error.code === 'VERSION_CONFLICT') {
    return { status: 409, code: error.code };
  }
  if (error.httpStatus === 412 || error.code === 'PRECONDITION_FAILED') {
    return { status: 412, code: error.code };
  }
  return null;
}

function ConnectionJob({ jobId, error }: { readonly jobId: string; readonly error: unknown }) {
  const job = useAsyncJob(jobId);
  if (error) return <Alert type="error" showIcon title={safeOperationError(error)} />;
  if (!jobId) return null;
  return (
    <Alert
      type="info"
      showIcon
      title={
        <span role="status">
          连接测试：{job.data?.status ?? 'QUEUED'}
          {job.connectionStatus !== 'connected' ? `（${job.connectionStatus}）` : ''}
        </span>
      }
    />
  );
}

function sourceSummaryValue(
  value: string | undefined,
  state: 'ready' | 'loading' | 'error' | 'unknown',
) {
  if (state === 'loading') return '加载中';
  if (state === 'error') return '暂不可用';
  if (state === 'unknown') return '未知';
  return value ?? '—';
}

const sourceTypeLabels: Readonly<Record<string, string>> = {
  ROBOT: '机器人',
  EDGE_AGENT: '边缘代理',
  OSS_IMPORT: 'OSS 导入',
};

const administrativeStateLabels: Readonly<Record<string, string>> = {
  ENABLED: '已启用',
  DISABLED: '已停用',
};

const connectivityLabels: Readonly<Record<string, string>> = {
  ONLINE: '在线',
  DEGRADED: '降级',
  OFFLINE: '离线',
  AUTH_FAILED: '认证失败',
  CONFIG_ERROR: '配置错误',
};

const credentialStateLabels: Readonly<Record<string, string>> = {
  CONFIGURED: '凭据已配置',
  MISSING: '凭据缺失',
  ROTATION_DUE: '凭据待轮换',
  EXPIRED: '凭据已过期',
  REVOKED: '凭据已撤销',
  INVALID: '凭据无效',
};

const sortLabels: Readonly<Record<DataSourcesSearch['sort'], string>> = {
  'updatedAt:desc': '最近更新',
  'name:asc': '按名称',
  'lastTestAt:desc': '最近测试',
};

function compactFilterSummary(search: DataSourcesSearch): string {
  const parts = [
    search.q ? `关键词“${search.q}”` : null,
    sourceTypeLabels[search.sourceType[0] ?? ''],
    administrativeStateLabels[search.administrativeState[0] ?? ''],
    connectivityLabels[search.connectivity[0] ?? ''],
    credentialStateLabels[search.credentialState[0] ?? ''],
  ].filter((part): part is string => Boolean(part));

  if (parts.length === 0) parts.push('全部数据源');
  parts.push(sortLabels[search.sort], `${search.limit} 条/页`);
  return parts.join(' · ');
}

function SourceMetricTile({
  eyebrow,
  label,
  value,
  detail,
  icon,
  tone = 'default',
}: Readonly<{
  eyebrow: string;
  label: string;
  value: ReactNode;
  detail: string;
  icon: ReactNode;
  tone?: 'default' | 'success' | 'warning';
}>) {
  const toneClass =
    tone === 'success'
      ? styles.sourceMetricSuccess
      : tone === 'warning'
        ? styles.sourceMetricWarning
        : '';
  return (
    <section className={`${styles.sourceMetric} ${toneClass}`} aria-label={label}>
      <span className={styles.sourceMetricIcon} aria-hidden="true">
        {icon}
      </span>
      <div className={styles.sourceMetricCopy}>
        <span className={styles.sourceMetricEyebrow}>{eyebrow}</span>
        <h2>{label}</h2>
        <strong>{value}</strong>
        <span className={styles.sourceMetricDetail}>{detail}</span>
      </div>
    </section>
  );
}

export default function DataSourcesPage() {
  const scope = useIngestScope();
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => dataSourcesQueryCodec.parse(params), [params]);
  const [draft, setDraft] = useState<FilterDraft>(() => filterDraft(search));
  const scopeKey = scope ? `${scope.organizationId}/${scope.projectId}/${scope.regionCode}` : null;
  const previousScopeKey = useRef<string | null | undefined>(undefined);

  useEffect(() => setDraft(filterDraft(search)), [search]);
  useEffect(() => {
    if (previousScopeKey.current !== undefined && previousScopeKey.current !== scopeKey) {
      setParams(
        dataSourcesQueryCodec.build(
          updateDataSourcesSearch(search, { sourceId: undefined, intent: undefined }, true),
        ),
        { replace: true },
      );
    }
    previousScopeKey.current = scopeKey;
  }, [scopeKey, search, setParams]);

  const canRead = capabilities.has('ingest_source.read');
  const canManage = capabilities.has('ingest_source.manage');
  const listFilters = useMemo(
    () => ({
      q: search.q,
      sourceType: search.sourceType,
      administrativeState: search.administrativeState,
      connectivity: search.connectivity,
      credentialState: search.credentialState,
      sort: search.sort,
      after: search.after,
      before: search.before,
      limit: search.limit,
    }),
    [search],
  );
  const page = useDataSourcesPage(scope, listFilters, canRead);
  const detail = useDataSource(scope, search.sourceId ?? null, canRead);
  const createMutation = useDataSourceMutation('create');
  const updateMutation = useDataSourceMutation('update');
  const rotateMutation = useDataSourceMutation('rotate-credential');
  const stateMutation = useDataSourceMutation(
    detail.data?.administrativeState === 'ENABLED' ? 'disable' : 'enable',
  );
  const testMutation = useTestDataSourceConnection();
  const [stateDialog, setStateDialog] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [editDialog, setEditDialog] = useState(false);
  const [rotateDialog, setRotateDialog] = useState(false);
  const [connectionJobId, setConnectionJobId] = useState('');

  const applySearch = useCallback(
    (patch: Partial<DataSourcesSearch>) => {
      const next = updateDataSourcesSearch(search, patch);
      setParams(dataSourcesQueryCodec.build(next));
    },
    [search, setParams],
  );

  const resetFilters = useCallback(() => {
    const next = filterDraft({
      ...search,
      q: undefined,
      sourceType: [],
      administrativeState: [],
      connectivity: [],
      credentialState: [],
      sort: 'updatedAt:desc',
      limit: 20,
    });
    setDraft(next);
    applySearch({
      q: undefined,
      sourceType: [],
      administrativeState: [],
      connectivity: [],
      credentialState: [],
      sort: 'updatedAt:desc',
      limit: 20,
    });
  }, [applySearch, search]);

  const createSource = (sourceDraft: SourceDraft) => {
    if (!scope) return;
    createMutation.mutate(
      {
        scope,
        idempotencyKey: createMutationIntentKey(),
        body: {
          name: sourceDraft.name,
          source_type: sourceDraft.configuration.kind,
          source_format: sourceDraft.sourceFormat,
          source_format_version: sourceDraft.sourceFormatVersion,
          binding: toWritableBindingWire(sourceDraft.binding),
          configuration: toWritableConnectorWire(sourceDraft.configuration),
          upload_policy_code: sourceDraft.uploadPolicyCode,
          ...(sourceDraft.credentialToken
            ? { credential_input: { kind: 'TOKEN', token: sourceDraft.credentialToken } }
            : {}),
        },
      },
      { onSuccess: (source) => applySearch({ intent: undefined, sourceId: source.id }) },
    );
  };

  const updateSource = (sourceDraft: SourceDraft) => {
    if (!scope || !detail.data || !sourceDraft.changeReason) return;
    updateMutation.mutate(
      {
        scope,
        sourceId: detail.data.id,
        etag: detail.data.etag,
        idempotencyKey: createMutationIntentKey(),
        body: {
          name: sourceDraft.name,
          source_format: sourceDraft.sourceFormat,
          source_format_version: sourceDraft.sourceFormatVersion,
          binding: toWritableBindingWire(sourceDraft.binding),
          configuration: toWritableConnectorWire(sourceDraft.configuration),
          upload_policy_code: sourceDraft.uploadPolicyCode,
          change_reason: sourceDraft.changeReason,
        },
      },
      { onSuccess: () => setEditDialog(false) },
    );
  };

  const editorInitial =
    detail.data && isConnectorEditable(detail.data.configuration)
      ? {
          name: detail.data.name,
          sourceFormat: detail.data.sourceFormat,
          sourceFormatVersion: detail.data.sourceFormatVersion,
          uploadPolicyCode: detail.data.uploadPolicy.code,
          configuration: detail.data.configuration,
          binding: (detail.data.binding.kind === 'ROBOT'
            ? { kind: 'ROBOT', robotId: detail.data.binding.robotId }
            : detail.data.binding.kind === 'EDGE_AGENT'
              ? { kind: 'EDGE_AGENT', agentId: detail.data.binding.agentId }
              : detail.data.binding.kind === 'OSS_IMPORT'
                ? { kind: 'OSS_IMPORT', sourceAlias: detail.data.binding.sourceAlias }
                : null) as WritableConnectorBinding | null,
        }
      : null;

  const statePreflight = useMemo<DangerPreflightEvidence | null>(() => {
    if (!detail.data || detail.dataUpdatedAt <= 0 || !scopeKey) return null;
    const preparedAt = new Date(detail.dataUpdatedAt);
    return {
      preparedAt: preparedAt.toISOString(),
      expiresAt: new Date(preparedAt.getTime() + 60_000).toISOString(),
      resourceVersion: detail.data.etag,
      scopeKey,
    };
  }, [detail.data, detail.dataUpdatedAt, scopeKey]);

  if (!scope && !unscopedAccount) {
    return <PageState state="feature-unavailable" label="数据源" />;
  }
  if (!canRead && !capabilities.loading && !unscopedAccount) {
    return <PageState state="forbidden" label="数据源" />;
  }

  const hasActiveFilters = Boolean(
    search.q ||
      search.sourceType.length ||
      search.administrativeState.length ||
      search.connectivity.length ||
      search.credentialState.length,
  );
  const activeFilterCount = [
    search.q,
    search.sourceType.length,
    search.administrativeState.length,
    search.connectivity.length,
    search.credentialState.length,
  ].filter(Boolean).length;
  const resolvedListState = unscopedAccount
    ? 'ready'
    : listState(page, hasActiveFilters);
  const summaryError = page.data?.componentErrors.find((error) => error.component === 'summary');
  const summaryState = unscopedAccount
    ? 'ready'
    : summaryError
      ? 'error'
      : page.isPending
        ? 'loading'
        : page.isError
          ? 'error'
          : page.data
            ? 'ready'
            : 'unknown';
  const sourceSummary = unscopedAccount
    ? {
        totalCount: '0',
        onlineCount: '0',
        verifiedBytesToday: '0',
        abnormalCount: '0',
      }
    : page.data?.summary;
  const table = (
    <DataSourceTable
      items={page.data?.items ?? []}
      selectedId={search.sourceId}
      onSelect={(sourceId) => applySearch({ sourceId })}
    />
  );
  const pager = page.data ? (
    <CursorPager
      pageInfo={page.data.pageInfo}
      busy={page.isFetching}
      windowLabel={`当前窗口 ${page.data.items.length} 条`}
      onChange={(cursor) => applySearch(cursor)}
    />
  ) : null;
  const listContent =
    resolvedListState === 'ready' ? (
      <div className={styles.listStack}>
        {table}
        {pager}
      </div>
    ) : resolvedListState === 'refreshing' ? (
      <PageState state="refreshing" label="数据源列表">
        <div className={styles.listStack}>
          {table}
          {pager}
        </div>
      </PageState>
    ) : (
      <PageState
        state={resolvedListState}
        label="数据源列表"
        requestId={requestId(page.error)}
        onRetry={page.isError ? () => void page.refetch() : undefined}
        action={
          resolvedListState === 'filtered-empty' ? (
            <Button onClick={resetFilters}>清除筛选</Button>
          ) : undefined
        }
      />
    );

  const detailState = detail.error ? stateFromError(detail.error) : null;
  const currentScopeKey = scopeKey ?? '';
  const stateConflict = conflictFrom(stateMutation.error);

  return (
    <main
      className={`${styles.page} ${search.sourceId ? styles.pageWithInspector : ''}`}
      data-page-id="P02"
    >
      <StandardPageScaffold
        header={{
          title: '数据源',
          breadcrumbs: [
            { key: 'ingest', label: '数据接入', to: routes.uploadRecords.build() },
            { key: 'sources', label: '数据源' },
          ],
          actions: (
            <>
              <Button
                href={routes.uploadJobs.build()}
                icon={<Upload aria-hidden="true" size={16} />}
              >
                上传任务
              </Button>
              {canManage && page.data?.allowedActions.includes('CREATE') ? (
                <Button
                  type="primary"
                  icon={<Plus aria-hidden="true" size={16} />}
                  onClick={() => {
                    createMutation.reset();
                    applySearch({ intent: 'create' });
                  }}
                >
                  新建数据源
                </Button>
              ) : null}
            </>
          ),
        }}
        summary={
          <div className={styles.sourceMetricStrip}>
            <SourceMetricTile
              eyebrow="CONNECTORS"
              label="数据源总数"
              value={sourceSummaryValue(sourceSummary?.totalCount, summaryState)}
              detail="已接入连接器"
              icon={<Database size={30} strokeWidth={1.65} />}
            />
            <SourceMetricTile
              eyebrow="ONLINE"
              label="在线"
              value={sourceSummaryValue(sourceSummary?.onlineCount, summaryState)}
              detail="最近心跳正常"
              icon={<Radio size={30} strokeWidth={1.65} />}
              tone="success"
            />
            <SourceMetricTile
              eyebrow="VERIFIED"
              label="今日验证数据量"
              value={
                summaryState === 'ready'
                  ? formatStorageSize(sourceSummary?.verifiedBytesToday)
                  : sourceSummaryValue(undefined, summaryState)
              }
              detail="通过完整性验证"
              icon={<HardDriveUpload size={30} strokeWidth={1.65} />}
            />
            <SourceMetricTile
              eyebrow="ISSUES"
              label="异常"
              value={sourceSummaryValue(sourceSummary?.abnormalCount, summaryState)}
              detail="需要人工处理"
              icon={<TriangleAlert size={30} strokeWidth={1.65} />}
              tone="warning"
            />
          </div>
        }
        filters={
          <FilterToolbar
            label="数据源筛选"
            collapsible
            collapseOnApply
            activeFilterCount={activeFilterCount}
            collapsedSummary={compactFilterSummary(search)}
            onApply={() =>
              applySearch({
                q: draft.q || undefined,
                sourceType: draft.sourceType ? [draft.sourceType] : [],
                administrativeState: draft.administrativeState ? [draft.administrativeState] : [],
                connectivity: draft.connectivity ? [draft.connectivity] : [],
                credentialState: draft.credentialState ? [draft.credentialState] : [],
                sort: draft.sort,
                limit: draft.limit,
              })
            }
            onReset={resetFilters}
            disabled={!unscopedAccount && page.isPending}
          >
            <label className={styles.filterField}>
              <span>搜索</span>
              <Input
                value={draft.q}
                placeholder="名称或稳定 ID"
                allowClear
                onChange={(event) => setDraft((current) => ({ ...current, q: event.target.value }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>连接器</span>
              <Select
                value={draft.sourceType}
                options={[
                  { label: '全部', value: '' },
                  { label: '机器人', value: 'ROBOT' },
                  { label: '边缘代理', value: 'EDGE_AGENT' },
                  { label: 'OSS 导入', value: 'OSS_IMPORT' },
                ]}
                onChange={(sourceType) => setDraft((current) => ({ ...current, sourceType }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>启用状态</span>
              <Select
                value={draft.administrativeState}
                options={[
                  { label: '全部', value: '' },
                  { label: '已启用', value: 'ENABLED' },
                  { label: '已停用', value: 'DISABLED' },
                ]}
                onChange={(administrativeState) =>
                  setDraft((current) => ({ ...current, administrativeState }))
                }
              />
            </label>
            <label className={styles.filterField}>
              <span>连通状态</span>
              <Select
                value={draft.connectivity}
                options={[
                  { label: '全部', value: '' },
                  { label: '在线', value: 'ONLINE' },
                  { label: '降级', value: 'DEGRADED' },
                  { label: '离线', value: 'OFFLINE' },
                  { label: '认证失败', value: 'AUTH_FAILED' },
                  { label: '配置错误', value: 'CONFIG_ERROR' },
                ]}
                onChange={(connectivity) => setDraft((current) => ({ ...current, connectivity }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>凭据状态</span>
              <Select
                value={draft.credentialState}
                options={[
                  { label: '全部', value: '' },
                  { label: '已配置', value: 'CONFIGURED' },
                  { label: '缺失', value: 'MISSING' },
                  { label: '待轮换', value: 'ROTATION_DUE' },
                  { label: '已过期', value: 'EXPIRED' },
                  { label: '已撤销', value: 'REVOKED' },
                  { label: '无效', value: 'INVALID' },
                ]}
                onChange={(credentialState) =>
                  setDraft((current) => ({ ...current, credentialState }))
                }
              />
            </label>
            <label className={styles.filterField}>
              <span>排序</span>
              <Select
                value={draft.sort}
                options={[
                  { label: '最近更新', value: 'updatedAt:desc' },
                  { label: '名称', value: 'name:asc' },
                  { label: '最近测试', value: 'lastTestAt:desc' },
                ]}
                onChange={(sort) => setDraft((current) => ({ ...current, sort }))}
              />
            </label>
            <label className={styles.filterField}>
              <span>每页</span>
              <Select
                value={draft.limit}
                options={[10, 20, 50].map((limit) => ({ label: String(limit), value: limit }))}
                onChange={(limit) => setDraft((current) => ({ ...current, limit }))}
              />
            </label>
          </FilterToolbar>
        }
        state={
          <div className={styles.contentStack}>
            <RobotIdentityConsole
              scope={scope}
              canRead={canRead}
              canManage={canManage}
            />
            {page.data?.componentErrors.map((error) => (
              <Alert
                type="error"
                showIcon
                key={`${error.component}-${error.requestId}`}
                title={`${error.component} 区域暂不可用`}
                description={
                  <span>
                    {error.message}（请求 ID：<code>{error.requestId}</code>）
                  </span>
                }
              />
            ))}
            {listContent}
          </div>
        }
      />

      <EntityDrawer
        open={Boolean(search.sourceId)}
        title={detail.data?.name ?? '数据源详情'}
        loading={detail.isPending && Boolean(search.sourceId)}
        width={330}
        onClose={() => {
          setStateDialog(false);
          setDeleteDialog(false);
          setEditDialog(false);
          setRotateDialog(false);
          applySearch({ sourceId: undefined });
        }}
      >
        <aside className={styles.inspector} aria-label="数据源详情">
          {detailState ? (
            <PageState
              state={detailState}
              label="数据源详情"
              requestId={requestId(detail.error)}
              onRetry={() => void detail.refetch()}
            />
          ) : detail.data ? (
            <Space orientation="vertical" size="middle" className={styles.inspectorStack}>
              <Typography.Text code>{detail.data.id}</Typography.Text>
              <Descriptions bordered column={1} size="small">
                <Descriptions.Item label="连接状态">
                  <StatusTag
                    status={
                      typeof detail.data.connectivity.state === 'string'
                        ? detail.data.connectivity.state
                        : detail.data.connectivity.state.raw
                    }
                    known={
                      typeof detail.data.connectivity.state === 'string' &&
                      detail.data.connectivity.state !== 'UNKNOWN'
                    }
                    tone={detail.data.connectivity.state === 'ONLINE' ? 'success' : 'warning'}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="来源类型">
                  {typeof detail.data.sourceType === 'string'
                    ? detail.data.sourceType
                    : detail.data.sourceType.raw}
                </Descriptions.Item>
                <Descriptions.Item label="数据格式">{detail.data.sourceFormat}</Descriptions.Item>
                <Descriptions.Item label="绑定对象">
                  {detail.data.binding.kind === 'ROBOT'
                    ? `机器人 ${detail.data.binding.robotId}`
                    : detail.data.binding.kind === 'EDGE_AGENT'
                      ? `边缘代理 ${detail.data.binding.agentId}`
                      : detail.data.binding.kind === 'OSS_IMPORT'
                        ? `OSS 导入 ${detail.data.binding.sourceAlias}`
                        : '未提供'}
                </Descriptions.Item>
                <Descriptions.Item label="配置版本">{detail.data.configVersion}</Descriptions.Item>
                <Descriptions.Item label="凭据">
                  {detail.data.credential.configured ? '已配置' : '未配置'}{' '}
                  {detail.data.credential.maskedHint}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  <time dateTime={detail.data.updatedAt}>{detail.data.updatedAt}</time>
                </Descriptions.Item>
                <Descriptions.Item label="最近心跳">
                  {detail.data.heartbeat?.lastSeenAt ? (
                    <time dateTime={detail.data.heartbeat.lastSeenAt}>
                      {detail.data.heartbeat.lastSeenAt}
                    </time>
                  ) : (
                    '暂无心跳'
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="最近上传">
                  {detail.data.lastUpload ? (
                    <time dateTime={detail.data.lastUpload.completedAt}>
                      {detail.data.lastUpload.completedAt}
                    </time>
                  ) : (
                    '暂无上传'
                  )}
                </Descriptions.Item>
              </Descriptions>
              {isUnknownEnum(detail.data.sourceType) ? (
                <Alert
                  type="warning"
                  showIcon
                  title="未知连接器类型"
                  description="当前仅支持只读查看；编辑、凭据轮换、测试与启停全部禁用。"
                />
              ) : null}
              <Flex gap="small" wrap="wrap">
                <Button
                  disabled={
                    !canManage ||
                    !editorInitial?.binding ||
                    detail.isFetching ||
                    !detail.data.allowedActions.includes('EDIT_CONFIGURATION')
                  }
                  onClick={() => {
                    updateMutation.reset();
                    setEditDialog(true);
                  }}
                >
                  编辑
                </Button>
                <Button
                  disabled={
                    !canManage ||
                    !canMutateDataSource(detail.data) ||
                    detail.isFetching ||
                    !detail.data.allowedActions.includes('ROTATE_CREDENTIAL')
                  }
                  onClick={() => {
                    rotateMutation.reset();
                    setRotateDialog(true);
                  }}
                >
                  轮换凭据
                </Button>
                <Button
                  disabled={
                    !canManage ||
                    !canMutateDataSource(detail.data) ||
                    !detail.data.allowedActions.includes('TEST_CONNECTION') ||
                    testMutation.isPending
                  }
                  loading={testMutation.isPending}
                  onClick={() => {
                    if (!scope) return;
                    testMutation.reset();
                    setConnectionJobId('');
                    testMutation.mutate(
                      {
                        scope,
                        sourceId: detail.data.id,
                        etag: detail.data.etag,
                        idempotencyKey: createMutationIntentKey(),
                        body: {
                          observed_config_version: detail.data.configVersion,
                          observed_credential_version: detail.data.credentialVersion,
                        },
                      },
                      {
                        onSuccess: (wire) => {
                          setConnectionJobId(wire.job.job_id);
                          void detail.refetch();
                        },
                      },
                    );
                  }}
                >
                  测试连接
                </Button>
                <Button
                  disabled={
                    !canManage ||
                    !canMutateDataSource(detail.data) ||
                    detail.isFetching ||
                    !detail.data.allowedActions.includes(
                      detail.data.administrativeState === 'ENABLED' ? 'DISABLE' : 'ENABLE',
                    )
                  }
                  onClick={() => {
                    stateMutation.reset();
                    setStateDialog(true);
                  }}
                >
                  {detail.data.administrativeState === 'ENABLED' ? '停用' : '启用'}
                </Button>
                {detail.data.allowedActions.includes('DELETE') ? (
                  <Button danger onClick={() => setDeleteDialog(true)}>
                    删除
                  </Button>
                ) : null}
              </Flex>
              <ConnectionJob jobId={connectionJobId} error={testMutation.error} />
            </Space>
          ) : null}
        </aside>
      </EntityDrawer>

      <SourceEditorDialog
        open={
          search.intent === 'create' &&
          canManage &&
          Boolean(page.data?.allowedActions.includes('CREATE'))
        }
        mode="create"
        pending={createMutation.isPending}
        errorMessage={safeOperationError(createMutation.error)}
        onClose={() => applySearch({ intent: undefined })}
        onSubmit={createSource}
      />
      {editorInitial?.binding ? (
        <SourceEditorDialog
          open={editDialog}
          mode="update"
          pending={updateMutation.isPending}
          errorMessage={safeOperationError(updateMutation.error)}
          initial={{ ...editorInitial, binding: editorInitial.binding }}
          onClose={() => setEditDialog(false)}
          onSubmit={updateSource}
        />
      ) : null}
      {detail.data ? (
        <CredentialRotationDialog
          open={rotateDialog}
          sourceId={detail.data.id}
          pending={rotateMutation.isPending}
          errorMessage={safeOperationError(rotateMutation.error)}
          onClose={() => setRotateDialog(false)}
          onConfirm={(token, reason) => {
            if (!scope) return;
            rotateMutation.mutate(
              {
                scope,
                sourceId: detail.data.id,
                etag: detail.data.etag,
                idempotencyKey: createMutationIntentKey(),
                body: { credential_input: { kind: 'TOKEN', token }, reason },
              },
              { onSuccess: () => setRotateDialog(false) },
            );
          }}
        />
      ) : null}
      {detail.data ? (
        <ConfirmSourceStateDialog
          open={stateDialog}
          sourceId={detail.data.id}
          action={detail.data.administrativeState === 'ENABLED' ? 'disable' : 'enable'}
          pending={stateMutation.isPending}
          blockedReasons={detail.data.blockedReasons}
          preflight={statePreflight}
          currentScopeKey={currentScopeKey}
          conflict={stateConflict}
          onClose={() => setStateDialog(false)}
          onResolveConflict={() => {
            stateMutation.reset();
            void detail.refetch();
          }}
          onConfirm={() => {
            if (!scope) return;
            stateMutation.mutate(
              {
                scope,
                sourceId: detail.data.id,
                etag: detail.data.etag,
                idempotencyKey: createMutationIntentKey(),
                body: {
                  reason: '用户确认',
                  expected_administrative_state: detail.data.administrativeState,
                },
              },
              { onSuccess: () => setStateDialog(false) },
            );
          }}
        />
      ) : null}
      {detail.data ? (
        <ConnectorDeleteConfirmDialog
          open={deleteDialog}
          sourceId={detail.data.id}
          blockedReasons={detail.data.blockedReasons}
          currentScopeKey={currentScopeKey}
          onClose={() => setDeleteDialog(false)}
        />
      ) : null}
    </main>
  );
}
