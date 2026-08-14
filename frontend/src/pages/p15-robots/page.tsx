import { Button, Input, Select } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { Box, Boxes, Radio, Search, ShieldCheck } from 'lucide-react';
import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ComponentTreeNode } from '../../entities/component';
import { routes as calibrationRoutes } from '../../features/calibrations/routing';
import { routes as dataSchemaRoutes } from '../../features/data-schemas/routing';
import {
  useComponentChannels,
  useComponentFrames,
  useRobotBootstrap,
  useRobotComponents,
  useRobots,
} from '../../features/robots/api';
import { buildComponentTree } from '../../features/robots/constraints';
import { isDomainError } from '../../shared/api/domain-error';
import {
  DataCursorPager,
  DataTable,
  DetailTabs,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from '../../shared/ui';
import workspace from '../ui-011e/workspace.module.css';
import { robotsQueryCodec } from './query-codec';

type RobotRow = NonNullable<ReturnType<typeof useRobots>['data']>['items'][number];

const detailTabs = [
  { id: 'overview', label: '基本信息' },
  { id: 'frames', label: '坐标系' },
  { id: 'channels', label: 'Channel' },
  { id: 'history', label: '维护记录' },
] as const;

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

function flattenVisible(
  nodes: readonly ComponentTreeNode[],
  expanded: ReadonlySet<string>,
  depth = 1,
): readonly { readonly node: ComponentTreeNode; readonly depth: number }[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(expanded.has(node.id) ? flattenVisible(node.children, expanded, depth + 1) : []),
  ]);
}

function AccessibleComponentTree({
  roots,
  selectedId,
  onSelect,
}: Readonly<{
  roots: readonly ComponentTreeNode[];
  selectedId?: string;
  onSelect: (id: string) => void;
}>) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(roots.map((root) => root.id)),
  );
  const visible = useMemo(() => flattenVisible(roots, expanded), [expanded, roots]);
  const [focusedId, setFocusedId] = useState(selectedId ?? visible[0]?.node.id ?? '');
  const refs = useRef(new Map<string, HTMLElement>());

  const focusAt = (index: number) => {
    const item = visible[Math.max(0, Math.min(index, visible.length - 1))];
    if (!item) return;
    setFocusedId(item.node.id);
    queueMicrotask(() => refs.current.get(item.node.id)?.focus());
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>, id: string) => {
    const index = visible.findIndex((item) => item.node.id === id);
    const item = visible[index];
    if (!item) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      focusAt(index + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      focusAt(index - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusAt(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusAt(visible.length - 1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      if (item.node.children.length > 0 && !expanded.has(id))
        setExpanded(new Set([...expanded, id]));
      else if (item.node.children.length > 0) focusAt(index + 1);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      if (expanded.has(id)) {
        const next = new Set(expanded);
        next.delete(id);
        setExpanded(next);
      } else if (item.node.parentComponentId)
        refs.current.get(item.node.parentComponentId)?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect(id);
    }
  };

  return (
    <div className={workspace.tree} role="tree" aria-label="组件拓扑">
      {visible.map(({ node, depth }) => (
        <div
          className={workspace.treeItem}
          role="treeitem"
          key={node.id}
          aria-level={depth}
          aria-selected={node.id === selectedId}
          aria-expanded={node.children.length ? expanded.has(node.id) : undefined}
        >
          <Button
            className={`${workspace.treeButton} ${workspace[`treeLevel${Math.min(depth, 4)}` as keyof typeof workspace]} ${node.id === selectedId ? workspace.treeSelected : ''}`}
            ref={(element) => {
              if (element) refs.current.set(node.id, element);
              else refs.current.delete(node.id);
            }}
            type="text"
            tabIndex={node.id === focusedId ? 0 : -1}
            onFocus={() => setFocusedId(node.id)}
            onKeyDown={(event) => onKeyDown(event, node.id)}
            onClick={() => onSelect(node.id)}
          >
            <span aria-hidden="true">
              {node.children.length ? (expanded.has(node.id) ? '▾' : '▸') : '•'}
            </span>
            {node.displayName}
          </Button>
        </div>
      ))}
    </div>
  );
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = robotsQueryCodec.parse(params);
  const [query, setQuery] = useState(search.q ?? '');
  const robots = useRobots(search.q ? { q: search.q } : {});
  const bootstrap = useRobotBootstrap(search.robotId ?? null);
  const components = useRobotComponents(search.robotId ?? null);
  const frames = useComponentFrames(search.componentId ?? null);
  const channels = useComponentChannels(search.componentId ?? null);
  const robotItems = robots.data?.items ?? [];
  const selectedRobot = robotItems.find((robot) => robot.id === search.robotId) ?? robotItems[0];
  const tree = buildComponentTree(components.data?.items ?? []);
  const selected = components.data?.items.find((component) => component.id === search.componentId);
  const calibrationReference = frames.data?.items[0];
  const schemaReference = channels.data?.items[0];

  const columns = useMemo<ColumnDef<RobotRow, unknown>[]>(
    () => [
      {
        id: 'displayName',
        header: '名称',
        size: 145,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() =>
              setParams(
                robotsQueryCodec.build(
                  { ...search, robotId: row.original.id, componentId: undefined },
                  search,
                ),
              )
            }
          >
            {row.original.displayName}
          </Button>
        ),
      },
      { id: 'serialNo', header: '序列号', size: 145, cell: ({ row }) => row.original.serialNo },
      {
        id: 'connectivity',
        header: '在线状态',
        size: 95,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.connectivity}
            label={row.original.connectivity === 'ONLINE' ? '在线' : row.original.connectivity}
            tone={row.original.connectivity === 'ONLINE' ? 'success' : 'warning'}
          />
        ),
      },
      { id: 'lifecycle', header: '生命周期', size: 100, cell: ({ row }) => row.original.lifecycle },
    ],
    [search, setParams],
  );

  const pageState = robots.isPending ? (
    <PageState state="loading" label="机器人与组件" />
  ) : robots.error && isDomainError(robots.error) ? (
    <PageState
      state={robots.error.httpStatus === 403 ? 'forbidden' : 'error'}
      onRetry={() => void robots.refetch()}
    />
  ) : null;

  return (
    <main className={workspace.page}>
      <StandardPageScaffold
        header={{
          title: '机器人与组件',
          description: '管理机器人实例、组件拓扑以及固定 Frame、Calibration 与 Schema 引用。',
          breadcrumbs: [
            { key: 'settings', label: '系统管理' },
            { key: 'robots', label: '机器人与组件' },
          ],
          actions: (
            <>
              <Button type="primary" disabled>
                新建机器人
              </Button>
              <Button disabled>添加组件</Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Boxes size={20} />}
              label="机器人实例"
              value={String(robotItems.length)}
            />
            <SummaryItem
              icon={<Radio size={20} />}
              label="在线"
              value={String(robotItems.filter((item) => item.connectivity === 'ONLINE').length)}
            />
            <SummaryItem
              icon={<Box size={20} />}
              label="已加载组件"
              value={String(components.data?.items.length ?? 0)}
            />
            <SummaryItem icon={<ShieldCheck size={20} />} label="关系语义" value="[from, to)" />
          </div>
        }
        filters={
          <FilterToolbar
            onApply={() =>
              setParams(
                robotsQueryCodec.build(
                  { ...search, q: query || undefined, after: undefined, before: undefined },
                  search,
                ),
              )
            }
            onReset={() => {
              setQuery('');
              setParams(
                robotsQueryCodec.build(
                  { ...search, q: undefined, after: undefined, before: undefined },
                  search,
                ),
              );
            }}
          >
            <label className={workspace.toolbarField}>
              <span>机器人名称</span>
              <Input.Search
                className={workspace.toolbarSearch}
                value={query}
                placeholder="搜索机器人"
                enterButton={
                  <Button aria-label="搜索机器人" icon={<Search aria-hidden="true" size={15} />} />
                }
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className={workspace.toolbarField}>
              <span>状态</span>
              <Select value="all" options={[{ value: 'all', label: '全部状态' }]} disabled />
            </label>
          </FilterToolbar>
        }
        state={pageState}
      >
        <div className={workspace.threePane}>
          <section className={workspace.pane} aria-label="机器人实例列表">
            <header className={workspace.paneHeader}>
              <div>
                <h2>机器人实例</h2>
                <p>稳定 ID 与服务端连接事实</p>
              </div>
              <span className={workspace.inlineMeta}>共 {robotItems.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={robotItems}
                columns={columns}
                getRowId={(robot) => robot.id}
                caption="机器人列表"
                state={robotItems.length ? 'ready' : 'empty'}
                empty={<PageState state={search.q ? 'filtered-empty' : 'empty'} />}
              />
            </div>
            {robots.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
                  pageInfo={{
                    startCursor: robots.data.pageInfo.start_cursor,
                    endCursor: robots.data.pageInfo.end_cursor,
                    hasPreviousPage: robots.data.pageInfo.has_previous_page,
                    hasNextPage: robots.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(robotsQueryCodec.build({ ...search, ...cursor }, search))
                  }
                  windowLabel={`当前 ${robotItems.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <section
            className={workspace.pane}
            aria-label="组件拓扑面板"
            aria-busy={components.isFetching}
          >
            <header className={workspace.paneHeader}>
              <div>
                <h2>组件拓扑</h2>
                <p>{selectedRobot?.displayName ?? '选择机器人'}</p>
              </div>
              <Button
                type="text"
                onClick={() => void components.refetch()}
                disabled={!search.robotId}
              >
                刷新
              </Button>
            </header>
            <div className={workspace.paneBody}>
              {tree.diagnostics.map((diagnostic, index) => (
                <p
                  className={workspace.warningNote}
                  role="alert"
                  key={`${diagnostic.kind}-${index}`}
                >
                  {diagnostic.kind}：拓扑写操作已关闭。
                </p>
              ))}
              {tree.roots.length ? (
                <AccessibleComponentTree
                  roots={tree.roots}
                  selectedId={search.componentId}
                  onSelect={(componentId) =>
                    setParams(robotsQueryCodec.build({ ...search, componentId }, search))
                  }
                />
              ) : search.robotId && components.isError ? (
                <div className={workspace.pageStateCompact}>
                  <PageState
                    state="feature-unavailable"
                    title="组件拓扑合同未在当前 Mock 开放"
                    description="机器人列表仍为真实冻结事实；不会生成示意组件。"
                  />
                </div>
              ) : (
                <div className={workspace.visualStage}>
                  <div className={workspace.visualStageCopy}>
                    <Boxes aria-hidden="true" size={44} />
                    <strong>{selectedRobot?.displayName ?? '选择机器人'}</strong>
                    <span>
                      选择固定 robotId 后加载授权组件树；键盘支持方向键、Home、End、Enter 与 Space。
                    </span>
                  </div>
                </div>
              )}
            </div>
          </section>

          <aside className={workspace.inspector} aria-label="机器人组件详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>{selected?.displayName ?? selectedRobot?.displayName ?? '组件详情'}</h2>
                <p>{selected ? selected.componentType : '机器人列表事实'}</p>
              </div>
              <StatusTag
                status={selected?.lifecycle ?? selectedRobot?.connectivity ?? 'UNKNOWN'}
                label={
                  selected?.lifecycle ??
                  (selectedRobot?.connectivity === 'ONLINE' ? '在线' : selectedRobot?.connectivity)
                }
                tone={
                  selected?.lifecycle === 'ACTIVE' || selectedRobot?.connectivity === 'ONLINE'
                    ? 'success'
                    : 'neutral'
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>稳定 ID</dt>
                  <dd>
                    <code>{selected?.id ?? selectedRobot?.id ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>序列号</dt>
                  <dd>{selected?.serialNo ?? selectedRobot?.serialNo ?? '—'}</dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>父组件</dt>
                  <dd>
                    <code>{selected?.parentComponentId ?? '根节点 / 未加载'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>模型引用</dt>
                  <dd>
                    <code>
                      {selected?.componentModelId ??
                        bootstrap.data?.effectiveModelBinding?.robotModelVersionId ??
                        '需 Bootstrap 授权'}
                    </code>
                  </dd>
                </div>
              </dl>
              <DetailTabs
                tabs={detailTabs}
                activeTab={search.tab}
                onChange={(tab) =>
                  setParams(
                    robotsQueryCodec.build({ ...search, tab: tab as typeof search.tab }, search),
                  )
                }
              />
              <section className={workspace.tabContent} role="tabpanel">
                {search.tab === 'overview' ? (
                  <p className={workspace.safeNote}>
                    关系区间统一使用 <code>[validFrom, validTo)</code>；本地重叠只作预校验，409/422
                    始终以服务端事实为准。
                  </p>
                ) : null}
                {search.tab === 'frames' ? (
                  <p className={workspace.featureNote}>
                    Frame 列表：{frames.data?.items.length ?? 0}；未授权时保持空，不推断坐标系。
                  </p>
                ) : null}
                {search.tab === 'channels' ? (
                  <p className={workspace.featureNote}>
                    Channel 列表：{channels.data?.items.length ?? 0}；Schema 版本必须是固定引用。
                  </p>
                ) : null}
                {search.tab === 'history' ? (
                  <PageState
                    state="feature-unavailable"
                    title="维护历史未在当前合同开放"
                    description="不伪造安装、更换或停用记录。"
                  />
                ) : null}
              </section>
              <div className={workspace.actionRow}>
                {calibrationReference ? (
                  <Button
                    href={calibrationRoutes.calibrations.build({
                      robotId: selected?.robotId ?? selectedRobot?.id ?? '',
                      componentId: selected?.id ?? '',
                      setId: calibrationReference.calibration_set_id,
                    })}
                  >
                    查看标定
                  </Button>
                ) : (
                  <Button disabled>标定管理</Button>
                )}
                {schemaReference ? (
                  <Button
                    href={dataSchemaRoutes.dataSchemas.build({
                      schemaId: schemaReference.schema_id,
                      schemaVersion: schemaReference.schema_version,
                      componentId: selected?.id ?? '',
                      detailTab: 'references',
                    })}
                  >
                    查看 Schema
                  </Button>
                ) : (
                  <Button disabled>Data Schema</Button>
                )}
              </div>
            </div>
          </aside>
        </div>
      </StandardPageScaffold>
    </main>
  );
}

export default Component;
