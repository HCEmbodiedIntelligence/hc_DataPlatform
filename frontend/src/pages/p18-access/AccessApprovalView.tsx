import { Alert, Badge, Button, Input, Pagination, Tabs } from "antd";
import { RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { isDomainError } from "../../shared/api/domain-error";
import {
  PageState,
  StandardPageScaffold,
  type PageStateKind,
} from "../../shared/ui";
import type { AccessDecisionInput } from "./access-api";
import { AccessRequestDetails } from "./components/AccessRequestDetails";
import { AccessRequestList } from "./components/AccessRequestList";
import { ApprovalDrawer } from "./components/ApprovalDrawer";
import type { AccessRequestRow } from "./contracts";
import type { AccessSearch, AccessTab } from "./query-codec";
import styles from "./styles.module.css";

export interface RequestCollectionState {
  readonly rows: readonly AccessRequestRow[];
  readonly loading: boolean;
  readonly fetching: boolean;
  readonly error: unknown;
}

export interface AccessApprovalViewProps {
  readonly search: AccessSearch;
  readonly membership: RequestCollectionState;
  readonly capability: RequestCollectionState;
  readonly canManage: boolean;
  readonly principalId: string | null;
  readonly decisionPending: boolean;
  readonly decisionError: unknown;
  readonly decisionSuccessMessage?: string;
  readonly onDecisionSuccessDismiss: () => void;
  readonly onSearchChange: (patch: Partial<AccessSearch>) => void;
  readonly onRefresh: (
    tab: "membership-requests" | "capability-requests",
  ) => void;
  readonly onDecision: (input: AccessDecisionInput) => void;
}

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  switch (error.code) {
    case "UNAUTHENTICATED":
    case "FORBIDDEN":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
    case "VERSION_CONFLICT":
    case "PRECONDITION_FAILED":
      return "conflict";
    case "RATE_LIMITED":
      return "rate-limited";
    case "NETWORK_ERROR":
      return "offline";
    case "CONTRACT_MISMATCH":
      return "contract-mismatch";
    default:
      return "error";
  }
}

function pendingCount(rows: readonly AccessRequestRow[]): number {
  return rows.reduce(
    (count, row) => count + Number(row.status === "PENDING"),
    0,
  );
}

function tabLabel(label: string, count?: number) {
  return (
    <span className={styles.tabLabel}>
      {label}
      {count && count > 0 ? (
        <Badge count={count} size="small" overflowCount={99} />
      ) : null}
    </span>
  );
}

function matchesSearch(row: AccessRequestRow, search: AccessSearch): boolean {
  if (search.status !== "ALL" && row.status !== search.status) return false;
  const query = search.q?.trim().toLocaleLowerCase("zh-CN");
  if (!query) return true;
  return [
    row.requestId,
    row.requesterId,
    row.projectId,
    row.reason ?? "",
    row.decisionReason ?? "",
    ...row.capabilityKeys,
  ].some((value) => value.toLocaleLowerCase("zh-CN").includes(query));
}

export function AccessApprovalView({
  canManage,
  capability,
  decisionError,
  decisionPending,
  decisionSuccessMessage,
  membership,
  onDecision,
  onDecisionSuccessDismiss,
  onRefresh,
  onSearchChange,
  principalId,
  search,
}: Readonly<AccessApprovalViewProps>) {
  const selectedTriggerRef = useRef<HTMLButtonElement>(null);
  const refreshButtonRef = useRef<HTMLButtonElement>(null);
  const successDismissRef = useRef<HTMLButtonElement>(null);
  const operationalTab = search.tab;
  const active = search.tab === "capability-requests" ? capability : membership;
  const filteredRows = useMemo(
    () =>
      [...active.rows]
        .filter((row) => matchesSearch(row, search))
        .sort((left, right) => {
          const direction = search.order === "recent" ? -1 : 1;
          return (
            direction *
            (Date.parse(left.createdAt) - Date.parse(right.createdAt))
          );
        }),
    [active.rows, search],
  );
  const pageCount = Math.max(
    1,
    Math.ceil(filteredRows.length / search.pageSize),
  );
  const page = Math.min(search.page, pageCount);
  const pageRows = filteredRows.slice(
    (page - 1) * search.pageSize,
    page * search.pageSize,
  );
  const selected =
    pageRows.find((row) => row.requestId === search.requestId) ??
    pageRows[0] ??
    null;
  const drawerOpen = search.drawer === "open" && selected !== null;

  const restoreDrawerFocus = useCallback(() => {
    globalThis.requestAnimationFrame(() => {
      const selectedTrigger = selectedTriggerRef.current;
      if (selectedTrigger?.isConnected) selectedTrigger.focus();
      else if (successDismissRef.current?.isConnected)
        successDismissRef.current.focus();
      else refreshButtonRef.current?.focus();
    });
  }, []);

  const closeDrawer = useCallback(() => {
    onSearchChange({ drawer: "closed" });
    restoreDrawerFocus();
  }, [onSearchChange, restoreDrawerFocus]);

  const drawerWasOpenRef = useRef(drawerOpen);
  useEffect(() => {
    const drawerWasOpen = drawerWasOpenRef.current;
    drawerWasOpenRef.current = drawerOpen;
    if (drawerWasOpen && !drawerOpen) restoreDrawerFocus();
  }, [drawerOpen, restoreDrawerFocus]);

  useEffect(() => {
    if (!drawerOpen) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeDrawer();
    };
    globalThis.addEventListener("keydown", closeOnEscape);
    return () => globalThis.removeEventListener("keydown", closeOnEscape);
  }, [closeDrawer, drawerOpen]);

  const tabs = [
    {
      key: "membership-requests",
      label: tabLabel("项目加入申请", pendingCount(membership.rows)),
    },
    {
      key: "capability-requests",
      label: tabLabel("权限申请", pendingCount(capability.rows)),
    },
  ];

  const filters = (
    <div className={styles.filters} role="search" aria-label="申请筛选">
      <label className={styles.filterField}>
        <span>搜索申请</span>
        <Input
          name="access-request-search"
          autoComplete="off"
          prefix={<Search aria-hidden="true" size={15} />}
          placeholder="申请人 ID、说明或 capability…"
          value={search.q ?? ""}
          allowClear
          onChange={(event) =>
            onSearchChange({ q: event.target.value || undefined, page: 1 })
          }
        />
      </label>
      <label className={styles.filterField}>
        <span>状态</span>
        <select
          name="access-request-status"
          value={search.status}
          onChange={(event) =>
            onSearchChange({
              status: event.target.value as AccessSearch["status"],
              page: 1,
            })
          }
        >
          <option value="ALL">全部状态</option>
          <option value="PENDING">待审批</option>
          <option value="APPROVED">已批准</option>
          <option value="REJECTED">已拒绝</option>
          <option value="WITHDRAWN">已撤回</option>
          <option value="REVOKED">已撤销</option>
        </select>
      </label>
      <label className={styles.filterField}>
        <span>排序</span>
        <select
          name="access-request-order"
          value={search.order}
          onChange={(event) =>
            onSearchChange({
              order: event.target.value as AccessSearch["order"],
              page: 1,
            })
          }
        >
          <option value="recent">最近提交</option>
          <option value="oldest">最早提交</option>
        </select>
      </label>
      <Button
        ref={refreshButtonRef}
        className={styles.refreshButton}
        icon={<RefreshCw aria-hidden="true" size={16} />}
        loading={active.fetching}
        onClick={() => onRefresh(operationalTab)}
      >
        刷新
      </Button>
    </div>
  );

  let content;
  if (active.loading && active.rows.length === 0) {
    content = <PageState state="loading" label="访问申请" layout="list" />;
  } else if (active.error && active.rows.length === 0) {
    content = (
      <PageState
        state={stateFromError(active.error)}
        label="访问申请"
        layout="list"
        description={
          isDomainError(active.error) ? active.error.message : undefined
        }
        requestId={isDomainError(active.error) ? active.error.requestId : null}
        onRetry={() => onRefresh(operationalTab)}
      />
    );
  } else if (filteredRows.length === 0) {
    content = (
      <PageState
        state={search.q || search.status !== "ALL" ? "filtered-empty" : "empty"}
        label={
          search.tab === "membership-requests" ? "项目加入申请" : "权限申请"
        }
        description={
          search.q || search.status !== "ALL"
            ? "当前筛选没有申请。清除搜索词或切换状态后重试。"
            : "当前项目作用域内没有可见申请。"
        }
      />
    );
  } else {
    content = (
      <div
        className={drawerOpen ? styles.workspaceWithDrawer : styles.workspace}
      >
        <section
          className={styles.requestWorkspace}
          data-e09-main
          aria-label="申请列表与详情"
        >
          {active.error ? (
            <PageState
              state="partial"
              label="访问申请"
              description="刷新失败，仍保留上次成功加载的申请。"
              onRetry={() => onRefresh(operationalTab)}
            >
              <div />
            </PageState>
          ) : null}
          <section
            className={styles.listPanel}
            aria-labelledby="request-list-heading"
          >
            <header className={styles.panelHeader}>
              <div>
                <h2 id="request-list-heading">申请列表</h2>
                <span>当前可见 {filteredRows.length} 条</span>
              </div>
            </header>
            <AccessRequestList
              rows={pageRows}
              selectedId={selected?.requestId}
              selectedTriggerRef={selectedTriggerRef}
              onSelect={(row) =>
                onSearchChange({ requestId: row.requestId, drawer: "open" })
              }
            />
            <div className={styles.paginationBlock}>
              <Pagination
                current={page}
                pageSize={search.pageSize}
                total={filteredRows.length}
                showSizeChanger
                pageSizeOptions={[10, 20]}
                showTotal={(total) => `共 ${total} 条`}
                onChange={(nextPage, nextPageSize) =>
                  onSearchChange({
                    page: nextPageSize === search.pageSize ? nextPage : 1,
                    pageSize: nextPageSize === 20 ? 20 : 10,
                    requestId: undefined,
                  })
                }
              />
              <small>
                正式列表合同暂未提供
                cursor；当前分页只作用于服务端已返回的可见申请。
              </small>
            </div>
          </section>
          <section
            className={styles.detailPanel}
            aria-labelledby="request-detail-heading"
          >
            <header className={styles.panelHeader}>
              <div>
                <h2 id="request-detail-heading">申请详情</h2>
                <span>服务端安全投影</span>
              </div>
            </header>
            <AccessRequestDetails row={selected} />
          </section>
        </section>
        {drawerOpen && selected ? (
          <ApprovalDrawer
            key={`${selected.kind}:${selected.requestId}`}
            row={selected}
            canManage={canManage}
            principalId={principalId}
            pending={decisionPending}
            error={decisionSuccessMessage ? null : decisionError}
            settled={Boolean(decisionSuccessMessage)}
            onClose={closeDrawer}
            onReload={() => onRefresh(operationalTab)}
            onDecision={onDecision}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className={styles.page} data-page-id="P18" data-e09-page>
      <StandardPageScaffold
        header={{
          title: "账户与权限",
          description:
            "在当前项目作用域审批加入项目和 capability 申请；注册账户不进入审批队列。",
          breadcrumbs: [
            { key: "security", label: "安全与审计" },
            { key: "access", label: "账户与权限" },
          ],
        }}
        filters={
          <div className={styles.tabAndFilters}>
            <Tabs
              activeKey={search.tab}
              aria-label="账户与权限功能"
              items={tabs}
              onChange={(value) =>
                onSearchChange({
                  tab: value as AccessTab,
                  q: undefined,
                  status: "ALL",
                  page: 1,
                  requestId: undefined,
                  drawer: "open",
                })
              }
            />
            {filters}
          </div>
        }
      >
        {decisionSuccessMessage ? (
          <Alert
            className={styles.decisionStatus}
            type="success"
            showIcon
            title={decisionSuccessMessage}
            role="status"
            aria-live="polite"
            aria-atomic="true"
            action={
              <Button
                ref={successDismissRef}
                type="text"
                size="small"
                onClick={onDecisionSuccessDismiss}
              >
                关闭成功提示
              </Button>
            }
          />
        ) : null}
        {content}
      </StandardPageScaffold>
    </div>
  );
}
