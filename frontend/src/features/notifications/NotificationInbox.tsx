import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Alert, Button, Segmented, Spin } from "antd";
import { Check, Inbox, RefreshCw } from "lucide-react";
import { useState } from "react";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  listAccountNotifications,
  markAccountNotificationRead,
  notificationQueryKeys,
  type AccountNotification,
  type AccountNotificationState,
} from "./api";
import styles from "./NotificationInbox.module.css";

const copy: Readonly<
  Record<AccountNotification["kind"], { title: string; description: string }>
> = {
  MEMBERSHIP_APPROVED: {
    title: "项目加入申请已通过",
    description: "你现在可以按已批准的范围进入该项目。",
  },
  MEMBERSHIP_REJECTED: {
    title: "项目加入申请未通过",
    description: "可在申请记录中查看后续状态。",
  },
  MEMBERSHIP_REVOKED: {
    title: "项目访问已撤销",
    description: "该项目的成员资格及其有效权限已停止。",
  },
  CAPABILITY_APPROVED: {
    title: "权限申请已通过",
    description: "新的项目权限将在下次会话同步后生效。",
  },
  CAPABILITY_REJECTED: {
    title: "权限申请未通过",
    description: "可在申请记录中查看后续状态。",
  },
  CAPABILITY_REVOKED: {
    title: "项目权限已撤销",
    description: "相应项目操作将不再可用。",
  },
  COLLECTION_TASK_CLOSED: {
    title: "采集任务已关闭",
    description: "该任务已停止接收新的数据包。",
  },
  COLLECTION_TASK_CANCELLED: {
    title: "采集任务已取消",
    description: "该任务已停止接收新的数据包，需重新开启后才能继续。",
  },
  COLLECTION_TASK_REOPENED: {
    title: "采集任务已重新开启",
    description: "该任务现在可以继续接收新的数据包。",
  },
  DATASET_VERSION_PUBLISHED: {
    title: "数据集版本已发布",
    description: "新的数据集版本已可供有权限的成员使用。",
  },
};

function displayTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "时间格式异常";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function notificationErrorMessage(reason: unknown): string {
  if (isDomainError(reason) && reason.httpStatus === 401)
    return "当前登录已失效，请重新登录后查看通知。";
  return "通知暂时无法加载，请稍后重试。";
}

export default function NotificationInbox() {
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  const [state, setState] = useState<AccountNotificationState | null>(null);
  const queryClient = useQueryClient();
  const query = useInfiniteQuery({
    queryKey: notificationQueryKeys.page(principalId ?? "signed-out", state),
    enabled: principalId !== null,
    staleTime: 30_000,
    retry: 1,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      listAccountNotifications(state, pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  const notifications = query.data?.pages.flatMap((page) => page.items) ?? [];
  const markRead = useMutation({
    mutationFn: markAccountNotificationRead,
    onSuccess: () => {
      if (principalId === null) return;
      void queryClient.invalidateQueries({
        queryKey: notificationQueryKeys.page(principalId, state),
      });
      void queryClient.invalidateQueries({
        queryKey: notificationQueryKeys.unread(principalId),
      });
    },
  });

  return (
    <section aria-label="账户通知" className={styles.inbox}>
      <div className={styles.toolbar}>
        <Segmented
          aria-label="通知筛选"
          options={[
            { label: "全部", value: "ALL" },
            { label: "未读", value: "UNREAD" },
          ]}
          value={state ?? "ALL"}
          onChange={(value) => setState(value === "UNREAD" ? "UNREAD" : null)}
        />
        <Button
          aria-label="刷新通知"
          icon={<RefreshCw aria-hidden="true" size={16} />}
          loading={query.isFetching && !query.isPending}
          type="text"
          onClick={() => void query.refetch()}
        />
      </div>

      {query.isPending ? (
        <div className={styles.centered} role="status">
          <Spin size="small" />
          <span>正在加载通知…</span>
        </div>
      ) : null}

      {query.isError ? (
        <Alert
          action={
            <Button
              size="small"
              type="link"
              onClick={() => void query.refetch()}
            >
              重试
            </Button>
          }
          message={notificationErrorMessage(query.error)}
          showIcon
          type="error"
        />
      ) : null}

      {notifications.length === 0 && !query.isPending && !query.isError ? (
        <div className={styles.empty}>
          <Inbox aria-hidden="true" size={26} strokeWidth={1.55} />
          <strong>
            {state === "UNREAD" ? "暂时没有未读通知" : "暂时没有通知"}
          </strong>
          <span>项目申请与重要业务事件会显示在这里。</span>
        </div>
      ) : null}

      {notifications.map((notification) => {
        const item = copy[notification.kind];
        const pending =
          markRead.isPending &&
          markRead.variables === notification.notification_id;
        return (
          <article
            className={styles.item}
            data-read={notification.state === "READ" || undefined}
            key={notification.notification_id}
          >
            <div className={styles.itemContent}>
              <div className={styles.itemTitleRow}>
                <strong>{item.title}</strong>
                {notification.state === "UNREAD" ? (
                  <span aria-label="未读" className={styles.unreadDot} />
                ) : null}
              </div>
              <p>{item.description}</p>
              <span className={styles.meta}>
                项目 {notification.project_id} ·{" "}
                {displayTime(notification.created_at)}
              </span>
            </div>
            {notification.state === "UNREAD" ? (
              <Button
                aria-label={`将“${item.title}”标记为已读`}
                icon={<Check aria-hidden="true" size={15} />}
                loading={pending}
                size="small"
                type="text"
                onClick={() => markRead.mutate(notification.notification_id)}
              >
                已读
              </Button>
            ) : null}
          </article>
        );
      })}

      {query.isFetchNextPageError ? (
        <Alert
          action={
            <Button
              size="small"
              type="link"
              onClick={() => void query.fetchNextPage()}
            >
              重试
            </Button>
          }
          message="下一页通知暂时无法加载。"
          showIcon
          type="error"
        />
      ) : null}

      {query.hasNextPage ? (
        <Button
          block
          className={styles.loadMore}
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? "正在加载更多通知…" : "加载更多通知"}
        </Button>
      ) : null}
    </section>
  );
}
