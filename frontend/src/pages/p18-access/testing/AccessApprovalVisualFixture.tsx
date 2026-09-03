import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { createDomainError } from "../../../shared/api/domain-error";
import { AccessApprovalView } from "../AccessApprovalView";
import { UserManagementPanel } from "../UserManagementPanel";
import type { AccessDecisionInput } from "../access-api";
import type {
  AccessRequestRow,
  AccessRequestStatus,
  ManagedAccount,
  ManagedAccountPage,
} from "../contracts";
import { decisionLabel } from "../presentation";
import type { AccessSearch } from "../query-codec";

export type AccessApprovalVisualScenario =
  | "reference"
  | "users"
  | "forbidden"
  | "empty";

export interface AccessApprovalVisualFixtureOptions {
  readonly scenario?: AccessApprovalVisualScenario;
  readonly tab?: "users" | "membership-requests" | "capability-requests";
}

const statuses: readonly AccessRequestStatus[] = [
  "PENDING",
  "PENDING",
  "APPROVED",
  "REJECTED",
  "WITHDRAWN",
  "REVOKED",
];

const membershipRows: readonly AccessRequestRow[] = Array.from(
  { length: 12 },
  (_, index) => ({
    kind: "membership",
    requestId: `membership-${String(index + 1).padStart(2, "0")}`,
    projectId: "project-robot-east-01",
    requesterId:
      index === 0
        ? "contractor-li.ming-017"
        : `user-${String(index + 37).padStart(3, "0")}@example.cn`,
    status: statuses[index % statuses.length] ?? "PENDING",
    reason:
      index === 0
        ? "参与机器人采集数据整理与标注协作"
        : "申请加入当前项目开展阶段性协作",
    decisionReason:
      index % statuses.length > 1 ? "项目负责人已按当前阶段核验" : null,
    decidedBy: index % statuses.length > 1 ? "project-admin-01" : null,
    capabilityKeys: [],
    createdAt: `2026-08-${String(18 - index).padStart(2, "0")}T0${index % 9}:20:00Z`,
    updatedAt: `2026-08-${String(18 - index).padStart(2, "0")}T0${index % 9}:35:00Z`,
    revision: index % statuses.length > 1 ? 2 : 1,
  }),
);

const capabilityRows: readonly AccessRequestRow[] = [
  {
    ...membershipRows[0]!,
    kind: "capability",
    requestId: "capability-01",
    requesterId: "internal-zhou.yu-008",
    reason: "负责正式版本审核与发布",
    capabilityKeys: ["dataset_version.publish", "access.manage"],
  },
  {
    ...membershipRows[1]!,
    kind: "capability",
    requestId: "capability-02",
    requesterId: "contractor-wang.qi-021",
    reason: "只读核验采集结果",
    capabilityKeys: ["dataset.read"],
  },
  {
    ...membershipRows[2]!,
    kind: "capability",
    requestId: "capability-03",
    requesterId: "internal-liu.an-012",
    capabilityKeys: ["annotation.review"],
  },
];

const managedAccounts: readonly ManagedAccount[] = [
  {
    principal_id: "principal-hc-admin",
    username: "hc-admin",
    display_name: "平台管理员",
    state: "ACTIVE",
    platform_role: "PLATFORM_ADMIN",
    recovery_email_configured: true,
    recovery_email_hint: "h***@example.cn",
    active_session_count: 2,
    password_changed_at: "2026-08-20T09:12:00Z",
    created_at: "2026-07-01T03:00:00Z",
    updated_at: "2026-08-20T09:12:00Z",
    revision: 8,
    etag: '"account-8"',
  },
  {
    principal_id: "principal-robot-ops",
    username: "robot-ops",
    display_name: "机器人运维",
    state: "ACTIVE",
    platform_role: "USER",
    recovery_email_configured: true,
    recovery_email_hint: "r***@example.cn",
    active_session_count: 3,
    password_changed_at: "2026-08-18T02:20:00Z",
    created_at: "2026-07-11T02:20:00Z",
    updated_at: "2026-08-18T02:20:00Z",
    revision: 4,
    etag: '"account-4"',
  },
  {
    principal_id: "principal-label-review",
    username: "label-review",
    display_name: "标注复核员",
    state: "DISABLED",
    platform_role: "USER",
    recovery_email_configured: false,
    recovery_email_hint: null,
    active_session_count: 0,
    password_changed_at: "2026-08-06T06:45:00Z",
    created_at: "2026-08-06T06:45:00Z",
    updated_at: "2026-08-19T11:30:00Z",
    revision: 3,
    etag: '"account-3"',
  },
];

const managedAccountPage: ManagedAccountPage = {
  items: [...managedAccounts],
  page: 1,
  page_size: 10,
  total: managedAccounts.length,
};

const forbiddenError = createDomainError({
  code: "FORBIDDEN",
  problemCode: "PROJECT_SCOPE_FORBIDDEN",
  message: "当前身份无权读取该项目的访问申请。",
  fieldErrors: [],
  operationErrors: [],
  blockedReasons: [],
  requestId: "visual-access-403",
  retryable: false,
  httpStatus: 403,
});

function AccessApprovalVisualFixture({
  scenario = "reference",
  tab = "membership-requests",
}: Readonly<AccessApprovalVisualFixtureOptions>) {
  const [search, setSearch] = useState<AccessSearch>({
    tab,
    status: "ALL",
    accountState: "ALL",
    accountRole: "ALL",
    order: "recent",
    page: 1,
    pageSize: 10,
    drawer: "closed",
  });
  const [memberships, setMemberships] = useState(membershipRows);
  const [capabilities, setCapabilities] = useState(capabilityRows);
  const [successMessage, setSuccessMessage] = useState<string>();
  const empty = scenario === "empty";
  const forbidden = scenario === "forbidden";
  const users = scenario === "users";

  const decide = (input: AccessDecisionInput) => {
    const removeDecidedRow = (rows: readonly AccessRequestRow[]) =>
      rows.filter((row) => row.requestId !== input.requestId);
    if (input.kind === "membership") setMemberships(removeDecidedRow);
    else setCapabilities(removeDecidedRow);
    setSuccessMessage(`${decisionLabel(input.action)}已由服务端确认。`);
    setSearch((current) => ({
      ...current,
      requestId: undefined,
      drawer: "closed",
    }));
  };

  return (
    <div data-p18-visual-fixture={scenario}>
      <AccessApprovalView
        search={search}
        membership={{
          rows: empty || forbidden ? [] : memberships,
          loading: false,
          fetching: false,
          error: forbidden ? forbiddenError : null,
        }}
        capability={{
          rows: empty || forbidden ? [] : capabilities,
          loading: false,
          fetching: false,
          error: forbidden ? forbiddenError : null,
        }}
        canManage
        canReadPlatformAccounts={users}
        userManagement={
          users ? (
            <UserManagementPanel
              search={search}
              page={managedAccountPage}
              loading={false}
              fetching={false}
              error={null}
              mutationPending={false}
              mutationError={null}
              canManage
              canUnlock
              currentPrincipalId="principal-hc-admin"
              onSearchChange={(patch) =>
                setSearch((current) => ({ ...current, ...patch }))
              }
              onRefresh={() => undefined}
              onCreate={() => Promise.resolve()}
              onStatusChange={() => Promise.resolve()}
              onRoleChange={() => Promise.resolve()}
              onResetPassword={() => Promise.resolve()}
              onDelete={() => Promise.resolve()}
              onUnlock={() => Promise.resolve()}
              onDismissStatus={() => undefined}
            />
          ) : undefined
        }
        principalId="project-admin-01"
        decisionPending={false}
        decisionError={null}
        decisionSuccessMessage={successMessage}
        onDecisionSuccessDismiss={() => setSuccessMessage(undefined)}
        onSearchChange={(patch) => {
          if (Object.keys(patch).some((key) => key !== "drawer"))
            setSuccessMessage(undefined);
          setSearch((current) => ({ ...current, ...patch }));
        }}
        onRefresh={() => setSuccessMessage(undefined)}
        onDecision={decide}
      />
    </div>
  );
}

const mountedRoots = new WeakMap<HTMLElement, Root>();

export function mountAccessApprovalVisualFixture(
  host: HTMLElement,
  options: AccessApprovalVisualFixtureOptions = {},
): void {
  mountedRoots.get(host)?.unmount();
  const root = createRoot(host);
  mountedRoots.set(host, root);
  root.render(<AccessApprovalVisualFixture {...options} />);
}
