import { Avatar, Button, Skeleton } from "antd";
import {
  Bell,
  Building2,
  ClipboardClock,
  FolderKanban,
  Settings,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  useAccountAccessOverview,
  type AccountAccessRequest,
} from "../../features/account/access-api";
import { useOwnAccountProfile } from "../../features/account/api";
import { useUnreadNotificationCount } from "../../features/notifications/api";
import { useShellStore } from "../../shared/scope/shell-store";
import { StandardPageScaffold } from "../../shared/ui/layout/StandardPageScaffold";
import { PageState } from "../../shared/ui/state/PageState";
import { StatusTag } from "../../shared/ui/state/StatusTag";
import styles from "./styles.module.css";

const requestKindLabels: Readonly<Record<AccountAccessRequest["kind"], string>> = {
  ORGANIZATION: "组织加入申请",
  PROJECT: "项目加入申请",
  CAPABILITY: "权限申请",
};
const requestStatusLabels: Readonly<Record<AccountAccessRequest["status"], string>> = {
  PENDING: "待审批",
  APPROVED: "已通过",
  REJECTED: "未通过",
  WITHDRAWN: "已撤回",
  REVOKED: "已撤销",
};

function formatInstant(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function AccountPage() {
  const profile = useOwnAccountProfile();
  const access = useAccountAccessOverview();
  const unread = useUnreadNotificationCount();
  const sessionScopes = useShellStore((state) => state.sessionScopes);
  const sessionOrganizations = useShellStore(
    (state) => state.sessionOrganizations,
  );
  const organizationCount =
    access.data?.organizations.length ?? sessionOrganizations.length;
  const projectCount = access.data?.projects.length ?? sessionScopes.length;
  const recentRequests = access.data?.requests.slice(0, 5) ?? [];

  if (profile.isError) {
    return (
      <StandardPageScaffold
        header={{ title: "个人主页", description: "查看个人账户和访问范围状态。" }}
        state={
          <PageState
            description="个人账户资料暂时无法加载。"
            label="个人主页"
            onRetry={() => void profile.refetch()}
            state="error"
          />
        }
      />
    );
  }
  if (profile.isPending || profile.data === undefined) {
    return (
      <StandardPageScaffold
        header={{ title: "个人主页", description: "查看个人账户和访问范围状态。" }}
        state={<PageState label="个人主页" layout="list" state="loading" />}
      />
    );
  }
  const account = profile.data.profile;
  const zeroMemberships = organizationCount === 0 && projectCount === 0;

  return (
    <StandardPageScaffold
      header={{
        title: "个人主页",
        description: "个人账户独立于组织、项目和业务权限，可随时使用。",
        actions: (
          <Button icon={<Settings aria-hidden="true" size={16} />}>
            <Link to="/account/settings">前往账户设置</Link>
          </Button>
        ),
      }}
    >
      <div className={styles.accountLayout}>
        <section className={styles.identityCard} aria-labelledby="account-identity-title">
          <Avatar className={styles.avatar} size={64}>
            {account.display_name.trim().slice(0, 1) || "账"}
          </Avatar>
          <div className={styles.identityCopy}>
            <p>PERSONAL ACCOUNT</p>
            <h2 id="account-identity-title">{account.display_name}</h2>
            <span translate="no">@{account.username}</span>
          </div>
          <StatusTag
            label={account.status === "ACTIVE" ? "账户正常" : "账户已停用"}
            status={account.status}
            tone={account.status === "ACTIVE" ? "success" : "danger"}
          />
        </section>

        <section className={styles.metrics} aria-label="账户访问摘要">
          <article>
            <Building2 aria-hidden="true" size={19} />
            <span>已加入组织</span>
            <strong>{organizationCount}</strong>
          </article>
          <article>
            <FolderKanban aria-hidden="true" size={19} />
            <span>已加入项目</span>
            <strong>{projectCount}</strong>
          </article>
          <article>
            <ClipboardClock aria-hidden="true" size={19} />
            <span>待处理申请</span>
            {access.isPending ? <Skeleton.Input active size="small" /> : <strong>{access.data?.pending_request_count ?? 0}</strong>}
          </article>
          <article>
            <Bell aria-hidden="true" size={19} />
            <span>未读通知</span>
            {unread.isPending ? <Skeleton.Input active size="small" /> : <strong>{unread.data ?? 0}</strong>}
          </article>
        </section>

        {zeroMemberships ? (
          <section className={styles.readyState} aria-labelledby="account-ready-title">
            <div className={styles.readyMark} aria-hidden="true">✓</div>
            <div>
              <h2 id="account-ready-title">个人账户已就绪</h2>
              <p>你尚未加入任何组织或项目，可以先使用个人账户功能，并在账户设置中提交加入申请。</p>
            </div>
            <Button type="primary">
              <Link to="/account/settings?tab=memberships">前往账户设置</Link>
            </Button>
          </section>
        ) : null}

        <section className={styles.requestsCard} aria-labelledby="recent-requests-title">
          <header>
            <div>
              <h2 id="recent-requests-title">最近申请状态</h2>
              <p>组织、项目与权限申请的最新进度。</p>
            </div>
          </header>
          {access.isError ? (
            <p className={styles.mutedState}>申请状态暂时无法加载，个人账户功能不受影响。</p>
          ) : recentRequests.length === 0 && !access.isPending ? (
            <p className={styles.mutedState}>暂无组织、项目或权限申请记录。</p>
          ) : access.isPending ? (
            <Skeleton active paragraph={{ rows: 2 }} title={false} />
          ) : (
            <ul>
              {recentRequests.map((item) => (
                <li key={item.request_id}>
                  <div>
                    <strong>{requestKindLabels[item.kind]}</strong>
                    <span>{item.project_id ? `${item.organization_id} / ${item.project_id}` : item.organization_id}</span>
                  </div>
                  <div>
                    <StatusTag
                      label={requestStatusLabels[item.status]}
                      status={item.status}
                      tone={item.status === "APPROVED" ? "success" : item.status === "REJECTED" || item.status === "REVOKED" ? "danger" : "neutral"}
                    />
                    <time dateTime={item.updated_at}>{formatInstant(item.updated_at)}</time>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </StandardPageScaffold>
  );
}

export default AccountPage;
