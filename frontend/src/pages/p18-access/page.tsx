import { Alert, Avatar, Button, Card, Descriptions, Grid, Input, Select, Space, Tabs, Typography } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAccessBootstrap, useProjectMembers, useRoleCapabilityCatalog, type ProjectMemberVm } from '../../features/access/api';
import { getCapabilityCounts, projectRoles, type ProjectRoleId } from '../../features/access/capability-catalog';
import { validateScopeGrant } from '../../features/access/scope-grants';
import { isDomainError } from '../../shared/api/domain-error';
import {
  DataCursorPager,
  DataTable,
  EntityDrawer,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
  type PageStateKind,
} from '../../shared/ui';
import { accessQueryCodec } from './query-codec';
import styles from './styles.module.css';

interface CapabilityMatrixRow {
  readonly key: string;
}

function roleName(roleId: ProjectMemberVm['roleId']): string {
  return projectRoles.find((role) => role.roleId === roleId)?.displayName ?? '未知角色';
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

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

export function Component() {
  const screens = Grid.useBreakpoint();
  const desktopInspector = Boolean(screens.xl);
  const [params, setParams] = useSearchParams();
  const search = accessQueryCodec.parse(params);
  const bootstrap = useAccessBootstrap();
  const memberFilters = useMemo<Record<string, string>>(() => ({
    ...(search.q ? { q: search.q } : {}),
    ...(search.after ? { after: search.after } : {}),
    ...(search.before ? { before: search.before } : {}),
    limit: String(search.limit),
  }), [search.after, search.before, search.limit, search.q]);
  const members = useProjectMembers(memberFilters);
  const catalog = useRoleCapabilityCatalog();
  const counts = getCapabilityCounts();
  const [matrixQuery, setMatrixQuery] = useState('');
  const [grantRole, setGrantRole] = useState<ProjectRoleId>('PROJECT_DEVELOPER');
  const [grantKey, setGrantKey] = useState('');

  const change = (patch: Partial<typeof search>) => {
    setParams(accessQueryCodec.build({ ...search, ...patch }));
  };

  const columns: ColumnDef<ProjectMemberVm, unknown>[] = [
    { id: 'displayName', header: '成员', cell: ({ row }) => <Button className={styles.memberButton} type="text" size="small" onClick={() => change({ memberId: row.original.id, invitationId: undefined })}><Avatar size={30}>{row.original.displayName.slice(0, 1)}</Avatar><span><strong>{row.original.displayName}</strong><small>{row.original.secondaryDisplay ?? row.original.principalId}</small></span></Button> },
    { id: 'identity', header: '安全标识', cell: ({ row }) => row.original.secondaryDisplay ?? row.original.principalId },
    { id: 'role', header: '角色', cell: ({ row }) => roleName(row.original.roleId) },
    { id: 'status', header: '状态', cell: ({ row }) => <StatusTag status={row.original.status} tone={row.original.status === 'ACTIVE' ? 'success' : row.original.status === 'UNKNOWN' ? 'warning' : 'neutral'} known={row.original.status !== 'UNKNOWN'} /> },
    { id: 'lastActiveAt', header: '最近活动', cell: ({ row }) => row.original.lastActiveAt ? <time>{row.original.lastActiveAt}</time> : '—' },
  ];

  const visibleCapabilities = useMemo(
    () => [...catalog.canonical].filter((key) => key.includes(matrixQuery.trim().toLowerCase())),
    [catalog.canonical, matrixQuery],
  );
  const matrixRows = useMemo<CapabilityMatrixRow[]>(() => visibleCapabilities.map((key) => ({ key })), [visibleCapabilities]);
  const matrixColumns = useMemo<ColumnDef<CapabilityMatrixRow, unknown>[]>(() => [
    { id: 'capability', header: 'Capability', cell: ({ row }) => <Typography.Text code>{row.original.key}</Typography.Text> },
    ...projectRoles.map((role) => ({
      id: role.roleId,
      header: `${role.displayName}（${counts.byRole[role.roleId]} 项）`,
      cell: ({ row }: { row: { original: CapabilityMatrixRow } }) => catalog.roleCeilings[role.roleId].has(row.original.key) ? '允许（ceiling）' : '拒绝',
    })),
  ], [catalog.roleCeilings, counts.byRole]);

  const selectedMember = members.data?.items.find((member) => member.id === search.memberId);
  const grantValidation = validateScopeGrant(
    { effect: 'DENY', capabilityKeys: grantKey ? [grantKey] : [] },
    catalog.roleCeilings[grantRole],
    catalog.roleCeilings[grantRole],
  );
  const bootstrapState: PageStateKind | 'ready' = bootstrap.isPending
    ? 'loading'
    : bootstrap.error ? stateFromError(bootstrap.error) : bootstrap.data ? 'ready' : 'empty';

  const membersContent = members.error ? (
    <PageState state={stateFromError(members.error)} label="项目成员" requestId={requestId(members.error)} onRetry={() => void members.refetch()} />
  ) : (
    <DataTable
      data={members.data?.items ?? []}
      columns={columns}
      getRowId={(member) => member.id}
      caption="项目成员"
      state={members.isPending ? 'loading' : undefined}
      empty={<PageState state={search.q ? 'filtered-empty' : 'empty'} label="项目成员" />}
    />
  );

  const tabContent = search.tab === 'members' ? membersContent : search.tab === 'roles' ? (
    <Card size="small" title="固定角色 capability ceiling">
      <label className={styles.matrixFilter}>筛选 capability<Input value={matrixQuery} onChange={(event) => setMatrixQuery(event.currentTarget.value)} allowClear /></label>
      <DataTable data={matrixRows} columns={matrixColumns} getRowId={(row) => row.key} caption="固定角色 capability ceiling" />
    </Card>
  ) : (
    <Card size="small" title="ScopeGrant 本地预校验">
      <Space orientation="vertical" size="middle" className={styles.policyForm}>
        <Alert type="info" showIcon title="ScopeGrant 只能在角色 ceiling 内收窄；服务端授权仍为最终事实。" />
        <label>角色<Select value={grantRole} onChange={setGrantRole} options={projectRoles.map((role) => ({ value: role.roleId, label: role.displayName }))} /></label>
        <label>Capability<Input value={grantKey} onChange={(event) => setGrantKey(event.currentTarget.value.trim())} placeholder="dataset.read" /></label>
        <Alert type={grantKey && !grantValidation.valid ? 'error' : 'info'} showIcon title={grantKey ? grantValidation.reason ?? 'DENY 可作为收窄操作；写入功能仍不可用。' : '输入 capability 查看本地约束。'} />
        <Typography.Paragraph>Policy revision：<Typography.Text code>{bootstrap.data?.policyRevision ?? '—'}</Typography.Text>；项目 ETag 不显示为可编辑字段。</Typography.Paragraph>
      </Space>
    </Card>
  );

  const content = bootstrapState === 'ready' ? tabContent : (
    <PageState state={bootstrapState} label="权限快照" requestId={requestId(bootstrap.error)} onRetry={bootstrap.error ? () => void bootstrap.refetch() : undefined} />
  );

  useEffect(() => {
    if (!desktopInspector || !search.memberId) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setParams((currentParams) => accessQueryCodec.build({ ...accessQueryCodec.parse(currentParams), memberId: undefined }));
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [desktopInspector, search.memberId, setParams]);

  const memberInspector = selectedMember ? (
    <Space orientation="vertical" size="middle" className={styles.drawerContent}>
      <section className={styles.memberIdentity}>
        <Avatar size={48}>{selectedMember.displayName.slice(0, 1)}</Avatar>
        <span><strong>{selectedMember.displayName}</strong><small>{selectedMember.status === 'ACTIVE' ? '● 活跃' : selectedMember.status}</small></span>
      </section>
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="稳定 ID"><Typography.Text code>{selectedMember.id}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="角色">{roleName(selectedMember.roleId)}</Descriptions.Item>
        <Descriptions.Item label="Role version"><Typography.Text code>{selectedMember.roleVersion}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="Allowed actions">{selectedMember.allowedActions.join('、') || '无（fail closed）'}</Descriptions.Item>
      </Descriptions>
      <section className={styles.keyCapabilities}><strong>关键能力</strong>{selectedMember.allowedActions.length ? selectedMember.allowedActions.map((action) => <StatusTag key={action} status={action} tone="success" />) : <StatusTag status="NONE" label="无（fail closed）" tone="warning" />}</section>
      {selectedMember.blockedReasons.map((reason) => <Alert type="error" showIcon key={reason.code} title={reason.code} description={reason.message} />)}
    </Space>
  ) : <PageState state="not-found" label="成员详情" description="所选成员不在当前数据窗口中，请关闭详情后重新选择。" />;

  const roleSummary = bootstrapState === 'ready' ? (
    <section className={styles.roleSummary} aria-label="固定角色摘要">
      {projectRoles.map((role) => (
        <button key={role.roleId} type="button" onClick={() => {
          setGrantRole(role.roleId);
          change({ tab: 'roles', memberId: undefined });
        }}>
          <span className={styles.roleCount}>{counts.byRole[role.roleId]}</span>
          <span><strong>{role.displayName}</strong><small>{counts.byRole[role.roleId]} 项 canonical capability ceiling</small></span>
        </button>
      ))}
    </section>
  ) : null;

  return (
    <main className={styles.page} data-page-id="P18">
      <StandardPageScaffold
        header={{
          title: '用户权限',
          description: '成员、固定角色、ScopeGrant 与 canonical capability 安全投影。',
          breadcrumbs: [{ key: 'settings', label: '系统管理', to: '/settings/robot-models' }, { key: 'access', label: '用户权限' }],
        }}
        summary={roleSummary}
        filters={bootstrapState === 'ready' ? (
          <div className={styles.filterStack}>
            <Alert type="warning" showIcon title="权限写入暂不可用" description="共享授权收敛 Owner 与写 API 尚未联合批准，因此本页不渲染角色、成员或 ScopeGrant 提交入口。" />
            <Tabs activeKey={search.tab} onChange={(value) => change({ tab: value as typeof search.tab, memberId: undefined })} items={[{ key: 'members', label: '成员' }, { key: 'roles', label: '固定角色' }, { key: 'policies', label: 'ScopeGrant' }]} aria-label="权限区域" />
            {search.tab === 'members' ? <FilterToolbar label="成员筛选" onReset={search.q ? () => change({ q: undefined, after: undefined, before: undefined }) : undefined} disabled={members.isFetching}>
              <label className={styles.filterField}>搜索成员<Input value={search.q ?? ''} onChange={(event) => change({ q: event.target.value || undefined, after: undefined, before: undefined })} placeholder="姓名或安全标识" allowClear /></label>
              <label className={styles.filterField}>每页<Select value={search.limit} onChange={(value) => change({ limit: value, after: undefined, before: undefined })} options={([20, 50, 100] as const).map((value) => ({ value, label: String(value) }))} /></label>
            </FilterToolbar> : null}
          </div>
        ) : undefined}
        pagination={search.tab === 'members' && members.data ? (
          <DataCursorPager
            pageInfo={{
              startCursor: members.data.pageInfo.start_cursor,
              endCursor: members.data.pageInfo.end_cursor,
              hasPreviousPage: members.data.pageInfo.has_previous_page,
              hasNextPage: members.data.pageInfo.has_next_page,
            }}
            busy={members.isFetching}
            windowLabel={`快照 ${members.data.snapshotAt}`}
            onChange={(request) => change('before' in request ? { before: request.before, after: undefined } : { after: request.after, before: undefined })}
          />
        ) : undefined}
      >
        <div className={styles.contentStack}>
          <div className={search.memberId && desktopInspector && search.tab === 'members' ? styles.membersInspectorLayout : undefined}>
            <div className={styles.membersMainColumn}>
              <div className={styles.membersRegion}>{content}</div>
              {bootstrapState === 'ready' && search.tab === 'members' ? (
                <Card size="small" title="角色能力边界" extra="固定三角色；以 canonical capability + allowed_actions 共同决定">
                  <div className={styles.roleBoundaryGrid}>
                    {projectRoles.map((role) => (
                      <section key={role.roleId}>
                        <strong>{role.displayName}</strong>
                        <Typography.Text code>{role.roleId}</Typography.Text>
                        <p>允许能力上限：{counts.byRole[role.roleId]} 项</p>
                        <StatusTag status="FAIL_CLOSED" label="资源状态与 scope 继续收窄" tone="info" />
                      </section>
                    ))}
                  </div>
                </Card>
              ) : null}
            </div>
            {search.memberId && desktopInspector && search.tab === 'members' ? (
              <aside className={styles.desktopInspector} role="dialog" aria-modal="false" aria-label={selectedMember?.displayName ?? '成员详情'}>
                <header><Typography.Title level={2}>{selectedMember?.displayName ?? '成员详情'}</Typography.Title><Button type="text" onClick={() => change({ memberId: undefined })}>关闭</Button></header>
                {memberInspector}
              </aside>
            ) : null}
          </div>
        </div>
      </StandardPageScaffold>
      <EntityDrawer open={Boolean(search.memberId) && !desktopInspector} title={selectedMember?.displayName ?? '成员详情'} onClose={() => change({ memberId: undefined })}>
        {memberInspector}
      </EntityDrawer>
    </main>
  );
}

export default Component;
