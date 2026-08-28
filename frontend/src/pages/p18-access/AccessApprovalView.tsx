import { Alert, Badge, Button, Input, Pagination, Select, Tabs } from "antd";
import { RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { isDomainError } from "../../shared/api/domain-error";
import {
  PageState,
  StandardPageScaffold,
  type PageStateKind,
} from "../../shared/ui";
import type { AccessDecisionInput } from "./access-api";
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
  readonly canReadPlatformAccounts?: boolean;
  readonly canReadProjectRequests?: boolean;
  readonly userManagement?: ReactNode;
  readonly principalId: string | null;
  readonly decisionPending: boolean;
  readonly decisionError: unknown;
  readonly decisionRequestId?: string;
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
    row.requesterId,
    row.reason ?? "",
    row.decisionReason ?? "",
    ...row.capabilityKeys,
  ].some((value) => value.toLocaleLowerCase("zh-CN").includes(query));
}

export function AccessApprovalView({
  canManage,
  canReadPlatformAccounts = false,
  canReadProjectRequests = true,
  capability,
  decisionError,
  decisionPending,
  decisionRequestId,
  decisionSuccessMessage,
  membership,
  onDecision,
  onDecisionSuccessDismiss,
  onRefresh,
  onSearchChange,
  principalId,
  search,
  userManagement,
}: Readonly<AccessApprovalViewProps>) {
  const selectedTriggerRef = useRef<HTMLButtonElement>(null);
  const lastTriggerRef = useRef<HTMLElement>(null);
  const refreshButtonRef = useRef<HTMLButtonElement>(null);
  const successDismissRef = useRef<HTMLButtonElement>(null);
  const onSearchChangeRef = useRef(onSearchChange);
  useEffect(() => {
    onSearchChangeRef.current = onSearchChange;
  }, [onSearchChange]);
  const operationalTab: Exclude<AccessTab, "users"> =
    search.tab === "capability-requests"
      ? "capability-requests"
      : "membership-requests";
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
    [active.rows, search.order, search.q, search.status],
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
  const selectionRequested =
    search.drawer === "open" && Boolean(search.requestId);
  const selected = selectionRequested
    ? (filteredRows.find((row) => row.requestId === search.requestId) ?? null)
    : null;
  const drawerOpen = selected !== null;
  const drawerRowRef = useRef<AccessRequestRow | null>(null);
  if (selected) drawerRowRef.current = selected;
  const drawerRow = drawerRowRef.current;
  const selectedDecisionMatches =
    selected !== null && selected.requestId === decisionRequestId;

  const restoreDrawerFocus = useCallback(() => {
    const trigger = selectedTriggerRef.current ?? lastTriggerRef.current;
    globalThis.requestAnimationFrame(() => {
      globalThis.requestAnimationFrame(() => {
        if (trigger?.isConnected) trigger.focus();
        else if (successDismissRef.current?.isConnected)
          successDismissRef.current.focus();
        else refreshButtonRef.current?.focus();
      });
    });
  }, []);

  useEffect(() => {
    if (drawerOpen && selectedTriggerRef.current)
      lastTriggerRef.current = selectedTriggerRef.current;
  }, [drawerOpen, selected]);

  const closeDrawer = useCallback(() => {
    onSearchChange({ requestId: undefined, drawer: "closed" });
    restoreDrawerFocus();
  }, [onSearchChange, restoreDrawerFocus]);

  const drawerWasOpenRef = useRef(drawerOpen);
  useEffect(() => {
    const drawerWasOpen = drawerWasOpenRef.current;
    drawerWasOpenRef.current = drawerOpen;
    if (drawerWasOpen && !drawerOpen) restoreDrawerFocus();
  }, [drawerOpen, restoreDrawerFocus]);

  useEffect(() => {
    if (
      search.tab === "users" ||
      search.drawer !== "open" ||
      active.loading ||
      active.fetching
    )
      return;
    if (!search.requestId || selected === null) {
      onSearchChangeRef.current({ requestId: undefined, drawer: "closed" });
    }
  }, [
    active.fetching,
    active.loading,
    search.drawer,
    search.requestId,
    search.tab,
    selected,
  ]);

  const closeForContextChange = useCallback(
    (patch: Partial<AccessSearch>) =>
      onSearchChange({
        ...patch,
        requestId: undefined,
        drawer: "closed",
      }),
    [onSearchChange],
  );

  const tabs = [
    ...(canReadPlatformAccounts
      ? [
          {
            key: "users",
            label: tabLabel("用户管理"),
          },
        ]
      : []),
    ...(canReadProjectRequests
      ? [
          {
            key: "membership-requests",
            label: tabLabel("项目加入申请", pendingCount(membership.rows)),
          },
          {
            key: "capability-requests",
            label: tabLabel("权限申请", pendingCount(capability.rows)),
          },
        ]
      : []),
  ];

  const filters =
    search.tab === "users" || !canReadProjectRequests ? null : (
      <div className={styles.filters} role="search" aria-label="申请筛选">
        <label className={`${styles.filterField} ${styles.searchFilterField}`}>
          <span className={styles.visuallyHidden}>搜索申请</span>
          <Input
            size="large"
            name="access-request-search"
            aria-label="搜索申请"
            autoComplete="off"
            spellCheck={false}
            prefix={<Search aria-hidden="true" size={16} />}
            placeholder="搜索申请人、说明或 capability…"
            value={search.q ?? ""}
            allowClear
            onChange={(event) =>
              closeForContextChange({
                q: event.target.value || undefined,
                page: 1,
              })
            }
          />
        </label>
        <label className={styles.filterField}>
          <span className={styles.visuallyHidden}>状态</span>
          <Select
            size="large"
            aria-label="状态"
            value={search.status}
            options={[
              { value: "ALL", label: "全部状态" },
              { value: "PENDING", label: "待审批" },
              { value: "APPROVED", label: "已批准" },
              { value: "REJECTED", label: "已拒绝" },
              { value: "WITHDRAWN", label: "已撤回" },
              { value: "REVOKED", label: "已撤销" },
            ]}
            onChange={(status) => closeForContextChange({ status, page: 1 })}
          />
        </label>
        <label className={styles.filterField}>
          <span className={styles.visuallyHidden}>排序</span>
          <Select
            size="large"
            aria-label="排序"
            value={search.order}
            options={[
              { value: "recent", label: "最近提交" },
              { value: "oldest", label: "最早提交" },
            ]}
            onChange={(order) => closeForContextChange({ order, page: 1 })}
          />
        </label>
        <Button
          ref={refreshButtonRef}
          className={styles.refreshButton}
          size="large"
          icon={<RefreshCw aria-hidden="true" size={16} />}
          loading={active.fetching}
          onClick={() => {
            closeForContextChange({});
            onRefresh(operationalTab);
          }}
        >
          刷新
        </Button>
      </div>
    );

  let content;
  if (search.tab === "users") {
    content =
      canReadPlatformAccounts && userManagement ? (
        userManagement
      ) : (
        <PageState
          state="forbidden"
          label="用户管理"
          description="当前会话没有平台用户查看权限。"
        />
      );
  } else if (!canReadProjectRequests) {
    content = (
      <PageState
        state="forbidden"
        label="项目访问申请"
        description="当前会话没有可用的项目作用域。"
      />
    );
  } else if (active.loading && active.rows.length === 0) {
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
    const hasActiveFilter = Boolean(search.q) || search.status !== "ALL";
    content = (
      <div className={styles.requestStack}>
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
          className={styles.requestQueue}
          data-e09-main
          aria-labelledby="request-list-heading"
        >
          <header className={styles.queueHeader}>
            <div>
              <h2 id="request-list-heading">申请列表</h2>
              <span>
                {hasActiveFilter ? "筛选结果" : "已加载"} {filteredRows.length}{" "}
                条
              </span>
            </div>
          </header>
          <AccessRequestList
            rows={pageRows}
            selectedId={selected?.requestId}
            selectedTriggerRef={selectedTriggerRef}
            summaryLabel={
              search.tab === "membership-requests" ? "申请说明" : "权限摘要"
            }
            onSelect={(row, trigger) => {
              lastTriggerRef.current = trigger;
              onSearchChange({ requestId: row.requestId, drawer: "open" });
            }}
          />
          {filteredRows.length > search.pageSize ? (
            <div className={styles.paginationBlock}>
              <Pagination
                current={page}
                pageSize={search.pageSize}
                total={filteredRows.length}
                showSizeChanger
                pageSizeOptions={[10, 20]}
                showTotal={(total) => `${total} 条筛选结果`}
                onChange={(nextPage, nextPageSize) =>
                  closeForContextChange({
                    page: nextPageSize === search.pageSize ? nextPage : 1,
                    pageSize: nextPageSize === 20 ? 20 : 10,
                  })
                }
              />
            </div>
          ) : null}
        </section>
      </div>
    );
  }

  return (
    <div className={styles.page} data-page-id="P18" data-e09-page>
      <StandardPageScaffold
        header={{
          title: "账户与权限",
          description: "管理平台账户，并处理当前项目的成员加入与能力授权申请。",
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
                closeForContextChange({
                  tab: value as AccessTab,
                  q: undefined,
                  status: "ALL",
                  page: 1,
                })
              }
            />
            {filters}
          </div>
        }
      >
        {search.tab !== "users" && decisionSuccessMessage ? (
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

      {drawerRow ? (
        <ApprovalDrawer
          key={`${drawerRow.kind}:${drawerRow.requestId}`}
          row={drawerRow}
          open={drawerOpen}
          canManage={canManage}
          principalId={principalId}
          pending={selectedDecisionMatches && decisionPending}
          error={
            selectedDecisionMatches && !decisionSuccessMessage
              ? decisionError
              : null
          }
          settled={selectedDecisionMatches && Boolean(decisionSuccessMessage)}
          onClose={closeDrawer}
          onAfterOpenChange={(open) => {
            if (!open) restoreDrawerFocus();
          }}
          onReload={() => onRefresh(operationalTab)}
          onDecision={onDecision}
        />
      ) : null}
    </div>
  );
}
