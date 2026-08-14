import { Button, Input, Select } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { Boxes, FileStack, ListChecks, Search, ShieldCheck } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { DataSchemaVersion } from '../../entities/data-schema';
import {
  useDataSchemas,
  useDataSchemaVersion,
  usePreflightDataSchemaPublish,
  usePublishDataSchema,
  useResolveDataSchemaRoute,
} from '../../features/data-schemas/api';
import {
  canPublishSchema,
  schemaTelemetryProjection,
} from '../../features/data-schemas/registry-rules';
import { dataSchemasQueryCodec } from '../../features/data-schemas/routing';
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
  StandardPageScaffold,
  StatusTag,
} from '../../shared/ui';
import workspace from '../ui-011e/workspace.module.css';
import { pageDataSchemasQueryCodec } from './query-codec';

const detailTabs = [
  { id: 'fields', label: '字段定义' },
  { id: 'encoding', label: '编码' },
  { id: 'compatibility', label: '兼容性' },
  { id: 'references', label: '引用' },
] as const;

const registryTabs = [
  { id: 'registry', label: 'Schema Registry' },
  { id: 'snapshots', label: '数据集快照' },
  { id: 'compatibility', label: '兼容性检查' },
] as const;

const categories = [
  '全部',
  '图像',
  '深度',
  '点云',
  '关节状态',
  '动作',
  '位姿',
  '力与力矩',
  'IMU',
  '触觉',
] as const;

interface PublishIntent {
  readonly key: string;
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

function definitionRows(
  definition: Readonly<Record<string, unknown>>,
): readonly { name: string; type: string; description: string }[] {
  const fields = definition.fields;
  if (!Array.isArray(fields)) return [];
  return fields.flatMap((field) => {
    if (typeof field !== 'object' || field === null) return [];
    const record = field as Readonly<Record<string, unknown>>;
    const text = (value: unknown, fallback: string) =>
      typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
    return [
      {
        name: text(record.name, '未命名'),
        type: text(record.type, 'unknown'),
        description: text(record.description, '合同未提供说明'),
      },
    ];
  });
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = pageDataSchemasQueryCodec.parse(params);
  const [query, setQuery] = useState(search.q ?? '');
  const [category, setCategory] = useState<(typeof categories)[number]>('全部');
  const strictRoute = dataSchemasQueryCodec.parse(params);
  const relation =
    strictRoute.kind === 'unresolved' || strictRoute.kind === 'resolved'
      ? strictRoute.params
      : null;
  const mustResolveRelation = Boolean(search.componentId);
  const routeResolution = useResolveDataSchemaRoute(mustResolveRelation ? relation : null);
  const list = useDataSchemas(search.q ? { q: search.q } : {});
  const detail = useDataSchemaVersion(search.schemaId ?? null, search.schemaVersion ?? null);
  const capabilities = useCapabilities();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const preflight = usePreflightDataSchemaPublish();
  const publish = usePublishDataSchema();
  const [intent, setIntent] = useState<PublishIntent | null>(null);
  const intentKey = useRef<string | null>(null);
  const items = list.data?.items ?? [];
  const listSelected =
    items.find(
      (item) => item.schemaId === search.schemaId && item.version === search.schemaVersion,
    ) ?? items[0];
  const selected = detail.data;
  const visibleDefinition = selected?.definition ?? listSelected?.definition ?? {};
  const fieldRows = definitionRows(visibleDefinition);

  const evidence =
    selected?.hash && selected.compatibilityResult
      ? {
          status: 'SUCCEEDED' as const,
          result: selected.compatibilityResult,
          baselineHash: selected.hash.value,
          targetHash: selected.hash.value,
          mode: selected.compatibilityMode,
          rulesetVersion: selected.hash.canonicalizationVersion,
        }
      : null;
  const decision = selected
    ? canPublishSchema(selected, evidence)
    : { allowed: false, reasons: ['请选择固定 Schema 版本。'] };
  const canPublish = Boolean(
    selected && decision.allowed && capabilities.has('data_schema.publish'),
  );

  const columns = useMemo<ColumnDef<DataSchemaVersion, unknown>[]>(
    () => [
      {
        id: 'displayName',
        header: 'Schema 名称',
        size: 175,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() =>
              setParams(
                pageDataSchemasQueryCodec.build(
                  {
                    ...search,
                    schemaId: row.original.schemaId,
                    schemaVersion: row.original.version,
                    componentId: undefined,
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
        id: 'logicalType',
        header: '逻辑类型',
        size: 105,
        cell: ({ row }) => row.original.logicalType,
      },
      { id: 'version', header: '版本', size: 65, cell: ({ row }) => `v${row.original.version}` },
      {
        id: 'shape',
        header: 'dtype / shape',
        size: 125,
        cell: ({ row }) => definitionRows(row.original.definition)[0]?.type ?? '由定义决定',
      },
      {
        id: 'mode',
        header: '兼容模式',
        size: 92,
        cell: ({ row }) => row.original.compatibilityMode,
      },
      {
        id: 'status',
        header: '状态',
        size: 95,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            label={row.original.status === 'PUBLISHED' ? '已发布' : row.original.status}
            tone={
              row.original.status === 'PUBLISHED'
                ? 'success'
                : row.original.status === 'UNKNOWN'
                  ? 'warning'
                  : 'neutral'
            }
          />
        ),
      },
    ],
    [search, setParams],
  );

  const startPreflight = () => {
    if (!selected?.hash || !evidence || !canPublish) return;
    const key = crypto.randomUUID();
    intentKey.current = key;
    preflight.mutate(
      {
        schemaId: selected.schemaId,
        schemaVersion: selected.version,
        etag: selected.etag,
        expectedHash: selected.hash.value,
        validationReportId: `${selected.schemaId}:${selected.version}:validation`,
        compatibilityCheckId: `${selected.schemaId}:${selected.version}:compatibility`,
        changeSummary: '发布不可变 Schema 版本',
        idempotencyKey: key,
      },
      {
        onSuccess(result) {
          if (
            !result.allowed ||
            !result.preflight_token ||
            !result.expires_at ||
            result.blockers.length
          ) {
            setIntent(null);
            return;
          }
          setIntent({
            key,
            token: result.preflight_token,
            impact:
              [...result.impacts, ...result.warnings].map((entry) => entry.message).join('；') ||
              '发布后定义与 canonical hash 不可原地修改。',
            preparedAt: new Date().toISOString(),
            expiresAt: result.expires_at,
            resourceVersion: result.resource_revision,
          });
        },
      },
    );
  };

  const pageState =
    list.isPending || capabilities.loading ? (
      <PageState state="loading" label="数据 Schema" />
    ) : list.error && isDomainError(list.error) ? (
      <PageState
        state={list.error.httpStatus === 403 ? 'forbidden' : 'error'}
        onRetry={() => void list.refetch()}
      />
    ) : mustResolveRelation && (strictRoute.kind === 'not-found' || !relation) ? (
      <PageState
        state="not-found"
        title="Schema 深链无效"
        description="schemaId/schemaVersion/componentId 必须完整，版本会移除 v 前缀后验证。"
      />
    ) : mustResolveRelation && routeResolution.error ? (
      <PageState
        state="not-found"
        title="组件未引用此 Schema 版本"
        description="引用解析失败，不会替换为 latest/current。"
      />
    ) : null;

  return (
    <main className={workspace.page}>
      <StandardPageScaffold
        header={{
          title: '数据 Schema',
          description: '管理 Channel 数据结构、语义角色与固定版本兼容性。',
          breadcrumbs: [
            { key: 'settings', label: '系统管理' },
            { key: 'schemas', label: '数据 Schema' },
          ],
          actions: (
            <>
              <Button
                type="primary"
                disabled={!canPublish || preflight.isPending}
                loading={preflight.isPending}
                onClick={startPreflight}
              >
                新建 Schema
              </Button>
              <Button disabled>导入定义</Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Boxes size={20} />}
              label="Schema 版本"
              value={String(items.length)}
            />
            <SummaryItem
              icon={<ShieldCheck size={20} />}
              label="已发布"
              value={String(items.filter((item) => item.status === 'PUBLISHED').length)}
            />
            <SummaryItem
              icon={<ListChecks size={20} />}
              label="兼容"
              value={String(
                items.filter(
                  (item) => item.compatibilityResult && item.compatibilityResult !== 'UNKNOWN',
                ).length,
              )}
            />
            <SummaryItem
              icon={<FileStack size={20} />}
              label="逻辑类型"
              value={String(new Set(items.map((item) => item.logicalType)).size)}
            />
          </div>
        }
        filters={
          <>
            <nav aria-label="Schema 区域">
              <DetailTabs
                tabs={registryTabs}
                activeTab={search.tab}
                onChange={(tab) =>
                  setParams(
                    pageDataSchemasQueryCodec.build(
                      { ...search, tab: tab as typeof search.tab },
                      search,
                    ),
                  )
                }
              />
            </nav>
            <FilterToolbar
              onApply={() =>
                setParams(
                  pageDataSchemasQueryCodec.build(
                    { ...search, q: query || undefined, after: undefined, before: undefined },
                    search,
                  ),
                )
              }
              onReset={() => {
                setQuery('');
                setCategory('全部');
                setParams(
                  pageDataSchemasQueryCodec.build(
                    { ...search, q: undefined, after: undefined, before: undefined },
                    search,
                  ),
                );
              }}
            >
              <label className={workspace.toolbarField}>
                <span>Schema 名称</span>
                <Input.Search
                  className={workspace.toolbarSearch}
                  value={query}
                  placeholder="搜索 Schema 名称"
                  enterButton={
                    <Button
                      aria-label="搜索 Schema 名称"
                      icon={<Search aria-hidden="true" size={15} />}
                    />
                  }
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <label className={workspace.toolbarField}>
                <span>逻辑类型</span>
                <Select value="all" options={[{ value: 'all', label: '全部' }]} disabled />
              </label>
              <label className={workspace.toolbarField}>
                <span>状态</span>
                <Select value="all" options={[{ value: 'all', label: '全部' }]} disabled />
              </label>
            </FilterToolbar>
          </>
        }
        state={pageState}
      >
        <div
          className={workspace.schemaPane}
          role="tabpanel"
          id={`tabpanel-${search.tab}`}
          aria-labelledby={`tab-${search.tab}`}
        >
          <nav className={workspace.categoryRail} aria-label="Schema 类别">
            <div className={workspace.categoryTitle}>Schema 类别</div>
            {categories.map((entry) => (
              <Button
                key={entry}
                type="text"
                className={`${workspace.categoryButton} ${entry === category ? workspace.categoryActive : ''}`}
                onClick={() => setCategory(entry)}
              >
                {entry}
              </Button>
            ))}
          </nav>

          <section className={workspace.pane} aria-label="Schema Registry">
            <header className={workspace.paneHeader}>
              <div>
                <h2>Schema Registry</h2>
                <p>{category} · 授权范围内固定版本</p>
              </div>
              <span className={workspace.inlineMeta}>共 {items.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={items}
                columns={columns}
                getRowId={(item) => `${item.schemaId}:${item.version}`}
                caption="Schema Registry"
                state={items.length ? 'ready' : 'empty'}
                empty={<PageState state={search.q ? 'filtered-empty' : 'empty'} />}
              />
            </div>
            {list.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
                  pageInfo={{
                    startCursor: list.data.pageInfo.start_cursor,
                    endCursor: list.data.pageInfo.end_cursor,
                    hasPreviousPage: list.data.pageInfo.has_previous_page,
                    hasNextPage: list.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(pageDataSchemasQueryCodec.build({ ...search, ...cursor }, search))
                  }
                  windowLabel={`当前 ${items.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <aside className={workspace.inspector} aria-label="Schema 版本详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>{selected?.displayName ?? listSelected?.displayName ?? '固定版本详情'}</h2>
                <p>
                  {listSelected
                    ? `${listSelected.schemaId}/v${listSelected.version}`
                    : '选择稳定 schemaId/version'}
                </p>
              </div>
              <StatusTag
                status={selected?.status ?? listSelected?.status ?? 'UNKNOWN'}
                label={
                  selected?.status === 'PUBLISHED' || listSelected?.status === 'PUBLISHED'
                    ? '已发布'
                    : (selected?.status ?? listSelected?.status)
                }
                tone={
                  selected?.status === 'PUBLISHED' || listSelected?.status === 'PUBLISHED'
                    ? 'success'
                    : 'warning'
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>Schema ID</dt>
                  <dd>
                    <code>{selected?.schemaId ?? listSelected?.schemaId ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Family</dt>
                  <dd>
                    <code>{selected?.familyId ?? listSelected?.familyId ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Canonical hash</dt>
                  <dd>
                    <code>{selected?.hash?.value ?? listSelected?.hash?.value ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>兼容模式</dt>
                  <dd>{selected?.compatibilityMode ?? listSelected?.compatibilityMode ?? '—'}</dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>遥测安全投影</dt>
                  <dd>
                    {selected
                      ? Object.keys(schemaTelemetryProjection(selected)).join('、')
                      : '固定 ID / 版本 / 状态'}
                  </dd>
                </div>
              </dl>
              <DetailTabs
                tabs={detailTabs.map((tab) => ({ ...tab, id: `detail-${tab.id}` }))}
                activeTab={`detail-${search.detailTab}`}
                onChange={(tabId) => {
                  const detailTab = tabId.replace(/^detail-/u, '') as typeof search.detailTab;
                  setParams(pageDataSchemasQueryCodec.build({ ...search, detailTab }, search));
                }}
              />
              <section
                className={workspace.tabContent}
                role="tabpanel"
                id={`tabpanel-detail-${search.detailTab}`}
                aria-labelledby={`tab-detail-${search.detailTab}`}
              >
                {search.detailTab === 'fields' ? (
                  fieldRows.length ? (
                    <table className={workspace.definitionTable}>
                      <thead>
                        <tr>
                          <th>字段名</th>
                          <th>类型</th>
                          <th>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fieldRows.map((field) => (
                          <tr key={field.name}>
                            <td>{field.name}</td>
                            <td>{field.type}</td>
                            <td>{field.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <PageState
                      state="empty"
                      title="定义中没有字段数组"
                      description="保留原始只读定义，不推导字段。"
                    />
                  )
                ) : null}
                {search.detailTab === 'encoding' ? (
                  <pre className={workspace.codeBlock} tabIndex={0} aria-label="Schema 定义，只读">
                    {JSON.stringify(visibleDefinition, null, 2)}
                  </pre>
                ) : null}
                {search.detailTab === 'compatibility' ? (
                  <p className={workspace.safeNote}>
                    Mode：{selected?.compatibilityMode ?? listSelected?.compatibilityMode ?? '—'}
                    ；结果：
                    {selected?.compatibilityResult ??
                      listSelected?.compatibilityResult ??
                      'NOT_RUN'}
                    。未知 verdict 必须只读。
                  </p>
                ) : null}
                {search.detailTab === 'references' ? (
                  <p className={workspace.featureNote}>
                    Component：<code>{search.componentId ?? '未从组件上下文进入'}</code>
                    ；引用计数与分页由服务端授权投影决定。
                  </p>
                ) : null}
              </section>
              {decision.reasons.map((reason) => (
                <p className={workspace.warningNote} role="note" key={reason}>
                  {reason}
                </p>
              ))}
              <p className={workspace.safeNote}>Schema 原文只进入只读定义视图，永不进入遥测。</p>
            </div>
          </aside>
        </div>
      </StandardPageScaffold>

      <DangerConfirmModal
        open={intent !== null}
        title="发布不可变 Schema 版本"
        actionLabel="确认发布"
        resourceId={selected ? `${selected.schemaId}:v${selected.version}` : ''}
        impact={intent?.impact ?? '缺少预检证据'}
        blockers={selected?.blockedReasons ?? []}
        preflight={
          intent
            ? {
                preparedAt: intent.preparedAt,
                expiresAt: intent.expiresAt,
                resourceVersion: intent.resourceVersion,
                scopeKey,
              }
            : null
        }
        currentScopeKey={scopeKey}
        pending={publish.isPending}
        onCancel={() => {
          setIntent(null);
          intentKey.current = null;
        }}
        onConfirm={() => {
          if (!selected || !intent || intentKey.current !== intent.key) return;
          publish.mutate(
            {
              schemaId: selected.schemaId,
              schemaVersion: selected.version,
              etag: selected.etag,
              idempotencyKey: intent.key,
              preflightToken: intent.token,
            },
            {
              onSettled: () => {
                setIntent(null);
                intentKey.current = null;
              },
            },
          );
        }}
      />
    </main>
  );
}

export default Component;
