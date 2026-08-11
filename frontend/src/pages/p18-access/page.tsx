import { useMemo, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router-dom';
import { useAccessBootstrap, useProjectMembers, useRoleCapabilityCatalog, type ProjectMemberVm } from '../../features/access/api';
import { getCapabilityCounts, projectRoles, type ProjectRoleId } from '../../features/access/capability-catalog';
import { validateScopeGrant } from '../../features/access/scope-grants';
import { isDomainError } from '../../shared/api/domain-error';
import { EmptyState, ErrorPanel, MetricCard, PageHeader, SkeletonBlock, StandardTable, StatusBadge } from '../../shared/ui';
import { accessQueryCodec } from './query-codec';
import './page.css';

function roleName(roleId: ProjectMemberVm['roleId']): string {
  return projectRoles.find((role) => role.roleId === roleId)?.displayName ?? '未知角色';
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = accessQueryCodec.parse(params);
  const bootstrap = useAccessBootstrap();
  const members = useProjectMembers(search.q ? { q: search.q } : {});
  const catalog = useRoleCapabilityCatalog();
  const counts = getCapabilityCounts();
  const [matrixQuery, setMatrixQuery] = useState('');
  const [grantRole, setGrantRole] = useState<ProjectRoleId>('PROJECT_DEVELOPER');
  const [grantKey, setGrantKey] = useState('');

  const columns = useMemo<ColumnDef<ProjectMemberVm, unknown>[]>(() => [
    { id: 'displayName', header: '成员', cell: ({ row }) => <button className="link-button" type="button" onClick={() => setParams(accessQueryCodec.build({ ...search, memberId: row.original.id, invitationId: undefined }, search))}>{row.original.displayName}</button> },
    { id: 'identity', header: '安全标识', cell: ({ row }) => row.original.secondaryDisplay ?? row.original.principalId },
    { id: 'role', header: '角色', cell: ({ row }) => roleName(row.original.roleId) },
    { id: 'status', header: '状态', cell: ({ row }) => <StatusBadge status={row.original.status} tone={row.original.status === 'ACTIVE' ? 'success' : row.original.status === 'UNKNOWN' ? 'warning' : 'neutral'} /> },
    { id: 'lastActiveAt', header: '最近活动', cell: ({ row }) => row.original.lastActiveAt ?? '—' },
  ], [search, setParams]);

  if (bootstrap.isPending) return <section className="management-page"><PageHeader title="用户权限" /><SkeletonBlock width="100%" height="28rem" label="权限快照加载中" /></section>;
  if (bootstrap.error && isDomainError(bootstrap.error)) return <section className="management-page"><PageHeader title="用户权限" /><ErrorPanel error={bootstrap.error} onRetry={() => void bootstrap.refetch()} /></section>;
  const selectedMember = members.data?.items.find((member) => member.id === search.memberId);
  const visibleCapabilities = [...catalog.canonical].filter((key) => key.includes(matrixQuery.trim().toLowerCase()));
  const grantValidation = validateScopeGrant({ effect: 'DENY', capabilityKeys: grantKey ? [grantKey] : [] }, catalog.roleCeilings[grantRole], catalog.roleCeilings[grantRole]);

  return (
    <section className="management-page">
      <PageHeader title="用户权限" description="成员、固定角色、ScopeGrant 与 canonical capability 安全投影" breadcrumbs={[{ label: '系统管理' }, { label: '用户权限' }]} />
      <div className="feature-unavailable" role="note"><strong>权限写入暂不可用。</strong> 共享授权收敛 Owner 与写 API 尚未联合批准，因此本页不渲染角色、成员或 ScopeGrant 提交入口。</div>
      <div className="metric-grid">
        <MetricCard label="有效成员" value={bootstrap.data?.activeMembers.toLocaleString('zh-CN') ?? '—'} detail={`快照 ${bootstrap.data?.asOf ?? '—'}`} />
        <MetricCard label="待处理邀请" value={bootstrap.data?.pendingInvitations.toLocaleString('zh-CN') ?? '—'} />
        <MetricCard label="Canonical capabilities" value={counts.canonical} detail={`目录版本 ${catalog.version}`} />
        <MetricCard label="保留候选（关闭）" value={counts.reserved} />
      </div>
      <nav className="local-tabs" aria-label="权限区域">{(['members', 'roles', 'policies'] as const).map((tab) => <button type="button" key={tab} aria-current={search.tab === tab ? 'page' : undefined} onClick={() => setParams(accessQueryCodec.build({ ...search, tab }, search))}>{tab}</button>)}</nav>
      {search.tab === 'members' ? <div className="workspace-grid">
        <section><StandardTable data={members.data?.items ?? []} columns={columns} getRowId={(member) => member.id} caption="项目成员" loading={members.isPending} empty={<EmptyState kind={search.q ? 'filtered-empty' : 'no-data'} />} /></section>
        <aside className="detail-panel"><h2>{selectedMember?.displayName ?? '选择成员'}</h2>{selectedMember ? <><p>稳定 ID：<code>{selectedMember.id}</code></p><p>角色：{roleName(selectedMember.roleId)}</p><p>Role version：<code>{selectedMember.roleVersion}</code></p><p>Allowed actions：{selectedMember.allowedActions.join('、') || '无（fail closed）'}</p>{selectedMember.blockedReasons.map((reason) => <p role="alert" key={reason.code}>{reason.code}：{reason.message}</p>)}</> : <p>成员与待处理邀请是独立事实；本表不会合成 INVITED Membership。</p>}</aside>
      </div> : null}
      {search.tab === 'roles' ? <section className="detail-panel capability-matrix"><h2>固定角色 capability ceiling</h2><label>筛选 capability <input value={matrixQuery} onChange={(event) => setMatrixQuery(event.currentTarget.value)} /></label><div className="matrix-scroll"><table><thead><tr><th scope="col">Capability</th>{projectRoles.map((role) => <th scope="col" key={role.roleId}>{role.displayName}<small>{counts.byRole[role.roleId]} 项</small></th>)}</tr></thead><tbody>{visibleCapabilities.map((key) => <tr key={key}><th scope="row"><code>{key}</code></th>{projectRoles.map((role) => <td key={role.roleId}>{catalog.roleCeilings[role.roleId].has(key) ? '允许（ceiling）' : '拒绝'}</td>)}</tr>)}</tbody></table></div></section> : null}
      {search.tab === 'policies' ? <section className="detail-panel"><h2>ScopeGrant 本地预校验</h2><p>ScopeGrant 只能在角色 ceiling 内收窄；服务端授权仍为最终事实。</p><label>角色 <select value={grantRole} onChange={(event) => setGrantRole(event.currentTarget.value as ProjectRoleId)}>{projectRoles.map((role) => <option value={role.roleId} key={role.roleId}>{role.displayName}</option>)}</select></label><label>Capability <input value={grantKey} onChange={(event) => setGrantKey(event.currentTarget.value.trim())} placeholder="dataset.read" /></label><p role={grantValidation.valid ? 'status' : 'alert'}>{grantKey ? grantValidation.reason ?? 'DENY 可作为收窄操作；写入功能仍不可用。' : '输入 capability 查看本地约束。'}</p><p>Policy revision：<code>{bootstrap.data?.policyRevision ?? '—'}</code>；项目 ETag 不显示为可编辑字段。</p></section> : null}
    </section>
  );
}

export default Component;
