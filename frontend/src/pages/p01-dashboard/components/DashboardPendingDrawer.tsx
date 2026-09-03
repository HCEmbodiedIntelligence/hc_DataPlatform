import type { DashboardPendingPage } from "../../../features/dashboard/types";
import {
  CursorPager,
  EntityDrawer,
  PageState,
  type PageStateKind,
} from "../../../shared/ui";
import { DashboardPendingList } from "./DashboardPendingList";
import styles from "../styles.module.css";

export function DashboardPendingDrawer({
  open,
  page,
  state,
  requestId,
  onRetry,
  onCursorChange,
  onClose,
}: Readonly<{
  open: boolean;
  page?: DashboardPendingPage;
  state: PageStateKind | "ready";
  requestId?: string | null;
  onRetry?: () => void;
  onCursorChange: (
    cursor: Readonly<{ after?: string; before?: string }>,
  ) => void;
  onClose: () => void;
}>) {
  const content = page ? (
    <div className={styles.contentStack}>
      <DashboardPendingList items={page.items} />
    </div>
  ) : null;

  return (
    <EntityDrawer
      open={open}
      title="全部待办"
      onClose={onClose}
      loading={state === "loading"}
      footer={
        page?.pageInfo && page.items.length > 0 ? (
          <CursorPager
            pageInfo={page.pageInfo}
            busy={state === "refreshing"}
            windowLabel={`当前窗口 ${page.items.length} 条`}
            onChange={onCursorChange}
          />
        ) : undefined
      }
    >
      {state === "ready" ? (
        content
      ) : state === "refreshing" ? (
        <PageState state="refreshing" label="分页待办">
          {content}
        </PageState>
      ) : state !== "loading" ? (
        <PageState
          state={state}
          label="分页待办"
          requestId={requestId}
          onRetry={onRetry}
        />
      ) : null}
    </EntityDrawer>
  );
}
