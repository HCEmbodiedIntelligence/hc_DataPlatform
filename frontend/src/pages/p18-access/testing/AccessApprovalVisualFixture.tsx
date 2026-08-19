import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { createDomainError } from "../../../shared/api/domain-error";
import { AccessApprovalView } from "../AccessApprovalView";
import type { AccessDecisionInput } from "../access-api";
import type { AccessRequestRow, AccessRequestStatus } from "../contracts";
import { decisionLabel } from "../presentation";
import type { AccessSearch } from "../query-codec";

export type AccessApprovalVisualScenario = "reference" | "forbidden" | "empty";

export interface AccessApprovalVisualFixtureOptions {
  readonly scenario?: AccessApprovalVisualScenario;
  readonly tab?: "membership-requests" | "capability-requests";
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
    capabilityKeys: ["datasets.publish", "project.access.manage"],
  },
  {
    ...membershipRows[1]!,
    kind: "capability",
    requestId: "capability-02",
    requesterId: "contractor-wang.qi-021",
    reason: "只读核验采集结果",
    capabilityKeys: ["datasets.read"],
  },
  {
    ...membershipRows[2]!,
    kind: "capability",
    requestId: "capability-03",
    requesterId: "internal-liu.an-012",
    capabilityKeys: ["annotation.review"],
  },
];

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
    order: "recent",
    page: 1,
    pageSize: 10,
    drawer: "open",
  });
  const [memberships, setMemberships] = useState(membershipRows);
  const [capabilities, setCapabilities] = useState(capabilityRows);
  const [successMessage, setSuccessMessage] = useState<string>();
  const empty = scenario === "empty";
  const forbidden = scenario === "forbidden";

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
