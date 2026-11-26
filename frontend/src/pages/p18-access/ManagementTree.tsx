import { useState, type Key } from "react";
import { Button, Tree } from "antd";
import type { DataNode } from "antd/es/tree";
import { Building2, ChevronDown, Folder, UserRound } from "lucide-react";
import { useQueries } from "@tanstack/react-query";
import styles from "./styles.module.css";
import { request } from "../../shared/api/http-client";

export type ScopeNode = { org: string; project?: string };
export type TreeMember = { principal_id: string; username: string; administrator: boolean };
type Organization = { organization_id: string; organization_name: string; can_manage: boolean };
type Project = { organization_id: string; project_id: string; project_name: string };
type Node = DataNode & { scope?: ScopeNode; member?: TreeMember; children?: Node[] };

function treeTitle(name: string, id?: string, note?: string) {
  const fullName = id && id !== name ? name + " · " + id : name;
  return <span className={styles.treeLabel} title={fullName}>
    <span className={styles.treeName}>{name}</span>
    {note && <span className={styles.treeNote}>{note}</span>}
  </span>;
}

const keyOf = (kind: string, scope: ScopeNode, user?: string) =>
  JSON.stringify([kind, scope.org, scope.project ?? null, user ?? null]);

export function ManagementTree({ organizations, projects, current, actor, revision, onSelect, projectOnly }: {
  organizations: Organization[]; projects: Project[]; current: ScopeNode; actor?: string; revision?: string | number;
  onSelect: (scope: ScopeNode, member?: TreeMember) => void; projectOnly: boolean;
}) {
  const [expanded, setExpanded] = useState<Key[]>();
  const [pages, setPages] = useState<Record<string, number>>({});
  const expandedKeys = expanded ?? (current.org ? [keyOf("org", { org: current.org }), ...(current.project ? [keyOf("project", current)] : [])] : []);
  const branches: { scope: ScopeNode; key: string; offset: number }[] = [];
  function addBranch(kind: string, scope: ScopeNode) {
    const key = keyOf(kind, scope);
    if (expandedKeys.includes(keyOf("org", { org: scope.org })) && (kind === "unassigned" || expandedKeys.includes(key))) {
      for (let n = 0; n <= (pages[key] ?? 0); n++) branches.push({ scope, key, offset: n * 50 });
    }
  }
  for (const org of organizations) {
    if (org.can_manage && !projectOnly) addBranch("unassigned", { org: org.organization_id });
  }
  for (const p of projects) addBranch("project", { org: p.organization_id, project: p.project_id });
  const reads = useQueries({ queries: branches.map(({ scope, offset }) => ({
    queryKey: ["management-members", actor, revision, scope.org, scope.project, offset, ...(scope.project ? [] : ["unassigned"])],
    queryFn: ({ signal }: { signal: AbortSignal }) => {
      const params = new URLSearchParams({ organization_id: scope.org, offset: String(offset) });
      if (scope.project) params.set("project_id", scope.project);
      else params.set("unassigned_only", "true");
      return request<TreeMember[]>({ method: "GET", path: `/account/managed-members?${params}`, scopeMode: "session", signal, cache: "no-store" });
    },
  })) });
  function people(scope: ScopeNode, key: string): Node[] {
    const children: Node[] = [];
    const seen = new Set<string>();
    branches.forEach((branch, index) => {
      if (branch.key !== key) return;
      const read = reads[index];
      if (!read) return;
      for (const m of read.data?.slice(0, 50) ?? []) {
        if (seen.has(m.principal_id)) continue;
        seen.add(m.principal_id);
        children.push({ key: keyOf("member", scope, m.principal_id), title: treeTitle(m.username, undefined, scope.project ? undefined : "未加入项目"),
          isLeaf: true, icon: <UserRound className={styles.personGlyph} size={15} aria-hidden />, scope, member: m });
      }
      if (read.isError) children.push({ key: key + "-retry-" + index, selectable: false, isLeaf: true,
        title: <Button type="link" onClick={() => void read.refetch()}>读取失败，重试</Button> });
      else if (read.isPending) children.push({ key: key + "-loading-" + branch.offset, title: "正在读取成员…", disabled: true, isLeaf: true });
      else if (branch.offset === (pages[key] ?? 0) * 50 && (read.data?.length ?? 0) > 50)
        children.push({ key: key + "-more", isLeaf: true, selectable: false, title: <Button type="link" onClick={() =>
          setPages(prev => ({ ...prev, [key]: (prev[key] ?? 0) + 1 }))}>加载更多成员</Button> });
    });
    return children.length || !scope.project ? children : [{ key: key + "-empty", title: "暂无成员", disabled: true, isLeaf: true }];
  }
  const data: Node[] = organizations.map(o => {
    const scope = { org: o.organization_id };
    const children: Node[] = projects.filter(p => p.organization_id === o.organization_id).map(p => {
      const projectScope = { ...scope, project: p.project_id };
      const key = keyOf("project", projectScope);
      return { key, scope: projectScope, title: treeTitle(p.project_name, p.project_id), icon: <Folder className={styles.projectGlyph} size={16} aria-hidden />, children: people(projectScope, key) };
    });
    if (o.can_manage && !projectOnly) {
      children.push(...people(scope, keyOf("unassigned", scope)));
    }
    return { key: keyOf("org", scope), scope, title: treeTitle(o.organization_name, o.organization_id),
      icon: <Building2 className={styles.orgGlyph} size={17} aria-hidden />, children,
      isLeaf: children.length === 0 && (projectOnly || !o.can_manage || expandedKeys.includes(keyOf("org",scope))) };
  });
  return <Tree<Node> aria-label="组织、项目与成员" className={styles.directoryTree} showIcon blockNode
    switcherIcon={<ChevronDown size={14} aria-hidden />} expandedKeys={expandedKeys}
    selectedKeys={current.org ? [keyOf(current.project ? "project" : "org", current)] : []}
    onExpand={keys => setExpanded(keys)} treeData={data} onSelect={(_, { node }) => {
      if (!node.scope) return;
      const key = node.key;
      if (!node.member && !expandedKeys.includes(key)) setExpanded([...expandedKeys, key]);
      onSelect(node.scope, node.member);
    }} />;
}
