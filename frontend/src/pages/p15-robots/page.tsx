import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router-dom';
import type { ComponentTreeNode } from '../../entities/component';
import { routes as calibrationRoutes } from '../../features/calibrations/routing';
import { routes as dataSchemaRoutes } from '../../features/data-schemas/routing';
import { useComponentChannels, useComponentFrames, useRobotBootstrap, useRobotComponents, useRobots } from '../../features/robots/api';
import { buildComponentTree } from '../../features/robots/constraints';
import { isDomainError } from '../../shared/api/domain-error';
import { EmptyState, ErrorPanel, PageHeader, SkeletonBlock, StandardTable, StatusBadge } from '../../shared/ui';
import { robotsQueryCodec } from './query-codec';
import './page.css';

type RobotRow = NonNullable<ReturnType<typeof useRobots>['data']>['items'][number];

function flattenVisible(nodes: readonly ComponentTreeNode[], expanded: ReadonlySet<string>, depth = 1): readonly { readonly node: ComponentTreeNode; readonly depth: number }[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(expanded.has(node.id) ? flattenVisible(node.children, expanded, depth + 1) : []),
  ]);
}

function AccessibleComponentTree({ roots, selectedId, onSelect }: { readonly roots: readonly ComponentTreeNode[]; readonly selectedId?: string; readonly onSelect: (id: string) => void }) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(roots.map((root) => root.id)));
  const visible = useMemo(() => flattenVisible(roots, expanded), [expanded, roots]);
  const [focusedId, setFocusedId] = useState(selectedId ?? visible[0]?.node.id ?? '');
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const focusAt = (index: number) => {
    const item = visible[Math.max(0, Math.min(index, visible.length - 1))];
    if (!item) return;
    setFocusedId(item.node.id);
    queueMicrotask(() => refs.current.get(item.node.id)?.focus());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, id: string) => {
    const index = visible.findIndex((item) => item.node.id === id);
    const item = visible[index];
    if (!item) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); focusAt(index + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); focusAt(index - 1); }
    else if (event.key === 'Home') { event.preventDefault(); focusAt(0); }
    else if (event.key === 'End') { event.preventDefault(); focusAt(visible.length - 1); }
    else if (event.key === 'ArrowRight') {
      event.preventDefault();
      if (item.node.children.length > 0 && !expanded.has(id)) setExpanded(new Set([...expanded, id]));
      else if (item.node.children.length > 0) focusAt(index + 1);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      if (expanded.has(id)) { const next = new Set(expanded); next.delete(id); setExpanded(next); }
      else if (item.node.parentComponentId) refs.current.get(item.node.parentComponentId)?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(id); }
  };
  return (
    <div role="tree" aria-label="组件拓扑">
      {visible.map(({ node, depth }) => (
        <div role="treeitem" key={node.id} aria-level={depth} aria-selected={node.id === selectedId} aria-expanded={node.children.length ? expanded.has(node.id) : undefined}>
          <button
            ref={(element) => { if (element) refs.current.set(node.id, element); else refs.current.delete(node.id); }}
            type="button"
            tabIndex={node.id === focusedId ? 0 : -1}
            style={{ paddingInlineStart: `${(depth - 1) * 1.25 + 0.5}rem` }}
            onFocus={() => setFocusedId(node.id)}
            onKeyDown={(event) => onKeyDown(event, node.id)}
            onClick={() => onSelect(node.id)}
          >
            <span aria-hidden="true">{node.children.length ? expanded.has(node.id) ? '▾' : '▸' : '•'}</span> {node.displayName}
          </button>
        </div>
      ))}
    </div>
  );
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = robotsQueryCodec.parse(params);
  const robots = useRobots(search.q ? { q: search.q } : {});
  const bootstrap = useRobotBootstrap(search.robotId ?? null);
  const components = useRobotComponents(search.robotId ?? null);
  const frames = useComponentFrames(search.componentId ?? null);
  const channels = useComponentChannels(search.componentId ?? null);

  const columns = useMemo<ColumnDef<RobotRow, unknown>[]>(() => [
    { id: 'displayName', header: '机器人', cell: ({ row }) => <button className="link-button" type="button" onClick={() => setParams(robotsQueryCodec.build({ ...search, robotId: row.original.id, componentId: undefined }, search))}>{row.original.displayName}</button> },
    { id: 'serialNo', header: '序列号', cell: ({ row }) => row.original.serialNo },
    { id: 'connectivity', header: '连接', cell: ({ row }) => <StatusBadge status={row.original.connectivity} tone={row.original.connectivity === 'ONLINE' ? 'success' : 'warning'} /> },
    { id: 'lifecycle', header: '生命周期', cell: ({ row }) => row.original.lifecycle },
  ], [search, setParams]);

  if (robots.isPending) return <section className="management-page"><PageHeader title="机器人与组件" /><SkeletonBlock width="100%" height="28rem" label="机器人加载中" /></section>;
  if (robots.error && isDomainError(robots.error)) return <section className="management-page"><PageHeader title="机器人与组件" /><ErrorPanel error={robots.error} onRetry={() => void robots.refetch()} /></section>;
  const tree = buildComponentTree(components.data?.items ?? []);
  const selected = components.data?.items.find((component) => component.id === search.componentId);
  const calibrationReference = frames.data?.items[0];
  const schemaReference = channels.data?.items[0];

  return (
    <section className="management-page">
      <PageHeader title="机器人与组件" description="当前拓扑与左闭右开时间化关系；服务端 409/422 始终为最终事实。" breadcrumbs={[{ label: '系统管理' }, { label: '机器人与组件' }]} />
      <div className="three-pane">
        <section>
          <h2>机器人</h2>
          <StandardTable data={robots.data?.items ?? []} columns={columns} getRowId={(robot) => robot.id} caption="机器人列表" empty={<EmptyState kind={search.q ? 'filtered-empty' : 'no-data'} />} />
        </section>
        <section className="tree-panel" aria-busy={components.isFetching}>
          <h2>组件拓扑</h2>
          {tree.diagnostics.map((diagnostic, index) => <p role="alert" key={`${diagnostic.kind}-${index}`}>{diagnostic.kind}：拓扑写操作已关闭。</p>)}
          {tree.roots.length ? <AccessibleComponentTree roots={tree.roots} selectedId={search.componentId} onSelect={(componentId) => setParams(robotsQueryCodec.build({ ...search, componentId }, search))} /> : <EmptyState kind="no-data" title="暂无组件" />}
        </section>
        <aside className="detail-panel">
          <h2>{selected?.displayName ?? bootstrap.data?.displayName ?? '选择组件'}</h2>
          {selected ? (
            <>
              <p>稳定 ID：<code>{selected.id}</code></p>
              <p>类型：{selected.componentType}</p>
              <p>父组件：{selected.parentComponentId ?? '根节点'}</p>
              <p>关系区间统一使用 <code>[validFrom, validTo)</code>，本地重叠只作预校验。</p>
              <div className="action-stack">
                {calibrationReference ? <a href={calibrationRoutes.calibrations.build({ robotId: selected.robotId, componentId: selected.id, setId: calibrationReference.calibration_set_id })}>查看标定</a> : <span>无已授权标定引用</span>}
                {schemaReference ? <a href={dataSchemaRoutes.dataSchemas.build({ schemaId: schemaReference.schema_id, schemaVersion: schemaReference.schema_version, componentId: selected.id, detailTab: 'references' })}>查看 Schema</a> : <span>无已授权 Schema 引用</span>}
              </div>
            </>
          ) : <p>从拓扑树选择组件查看固定关系。</p>}
        </aside>
      </div>
    </section>
  );
}

export default Component;
