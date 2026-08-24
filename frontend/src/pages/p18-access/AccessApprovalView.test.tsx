// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  createDomainError,
  type DomainErrorCode,
} from "../../shared/api/domain-error";
import {
  AccessApprovalView,
  type AccessApprovalViewProps,
} from "./AccessApprovalView";
import type { AccessRequestRow } from "./contracts";
import type { AccessSearch } from "./query-codec";

const pendingMembership: AccessRequestRow = {
  kind: "membership",
  requestId: "membership-pending",
  projectId: "project-a",
  requesterId: "contractor-17",
  status: "PENDING",
  reason: "参与当前项目的数据整理",
  decisionReason: null,
  decidedBy: null,
  capabilityKeys: [],
  createdAt: "2026-08-18T01:00:00Z",
  updatedAt: "2026-08-18T01:00:00Z",
  revision: 1,
};
const approvedMembership: AccessRequestRow = {
  ...pendingMembership,
  requestId: "membership-approved",
  requesterId: "internal-8",
  status: "APPROVED",
  decidedBy: "admin-1",
  decisionReason: "项目负责人确认",
  revision: 2,
};
const riskyCapability: AccessRequestRow = {
  ...pendingMembership,
  kind: "capability",
  requestId: "capability-risky",
  requesterId: "internal-9",
  reason: "负责版本发布",
  capabilityKeys: ["datasets.publish", "project.access.manage"],
};

const defaultSearch: AccessSearch = {
  tab: "membership-requests",
  status: "ALL",
  accountState: "ALL",
  accountRole: "ALL",
  order: "recent",
  page: 1,
  pageSize: 10,
  drawer: "open",
};

function domainError(status: 403 | 409 | 429) {
  const code: DomainErrorCode =
    status === 403
      ? "FORBIDDEN"
      : status === 409
        ? "VERSION_CONFLICT"
        : "RATE_LIMITED";
  return createDomainError({
    code,
    problemCode: `ACCESS_${status}`,
    message: `访问申请操作失败 ${status}`,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: `request-${status}`,
    retryable: status === 429,
    httpStatus: status,
  });
}

function renderView(overrides: Partial<AccessApprovalViewProps> = {}) {
  const props: AccessApprovalViewProps = {
    search: defaultSearch,
    membership: {
      rows: [pendingMembership, approvedMembership],
      loading: false,
      fetching: false,
      error: null,
    },
    capability: {
      rows: [riskyCapability],
      loading: false,
      fetching: false,
      error: null,
    },
    canManage: true,
    principalId: "admin-1",
    decisionPending: false,
    decisionError: null,
    decisionRequestId: pendingMembership.requestId,
    onDecisionSuccessDismiss: vi.fn(),
    onSearchChange: vi.fn(),
    onRefresh: vi.fn(),
    onDecision: vi.fn(),
    ...overrides,
  };
  return { ...render(<AccessApprovalView {...props} />), props };
}

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
});

afterEach(cleanup);

describe("P18 E09 access approval view", () => {
  it("exposes only project joining and capability approval queues, never registration approval", () => {
    renderView();

    const tabs = screen.getByRole("tablist");
    expect(
      within(tabs).getByRole("tab", { name: /项目加入申请/u }),
    ).toBeVisible();
    expect(within(tabs).getByRole("tab", { name: /权限申请/u })).toBeVisible();
    expect(screen.getByText(/注册账户不进入审批队列/u)).toBeVisible();
    expect(
      screen.queryByRole("tab", { name: /用户注册审批/u }),
    ).not.toBeInTheDocument();
  });

  it("gates manager actions by capability and allows an applicant to withdraw the same request", () => {
    renderView({ canManage: false, principalId: "contractor-17" });

    expect(
      screen.getByRole("button", { name: "撤回申请", pressed: true }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "批准申请" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "拒绝申请" }),
    ).not.toBeInTheDocument();
  });

  it("offers revoke only for an approved request", () => {
    renderView({
      search: { ...defaultSearch, requestId: approvedMembership.requestId },
    });

    expect(
      screen.getByRole("button", { name: "撤销授权", pressed: true }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "批准申请" }),
    ).not.toBeInTheDocument();
  });

  it("shows high-impact facts without inventing a reauthentication input", () => {
    renderView({ search: { ...defaultSearch, tab: "capability-requests" } });

    expect(screen.getByText("请求包含高影响能力")).toBeVisible();
    expect(screen.queryByText(/尚未开放/u)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/密码|动态码|验证码/u),
    ).not.toBeInTheDocument();
  });

  it("requires a reason for rejection before emitting the formal decision", async () => {
    const user = userEvent.setup();
    const { props } = renderView();

    await user.click(
      screen.getByRole("button", { name: "拒绝申请", pressed: false }),
    );
    await user.click(screen.getByRole("button", { name: "提交拒绝申请" }));
    expect(screen.getByText("拒绝、撤销或撤回必须填写原因。")).toBeVisible();
    expect(props.onDecision).not.toHaveBeenCalled();

    await user.type(
      screen.getByRole("textbox", { name: /处理原因/u }),
      "项目材料不完整",
    );
    await user.click(screen.getByRole("button", { name: "提交拒绝申请" }));
    expect(props.onDecision).toHaveBeenCalledWith({
      kind: "membership",
      action: "reject",
      projectId: "project-a",
      requestId: "membership-pending",
      reason: "项目材料不完整",
    });
  });

  it("resets paging when a list filter changes", () => {
    const { props } = renderView({ search: { ...defaultSearch, page: 3 } });

    fireEvent.change(screen.getByLabelText("状态"), {
      target: { value: "APPROVED" },
    });
    expect(props.onSearchChange).toHaveBeenCalledWith({
      status: "APPROVED",
      page: 1,
    });
  });

  it("disables the stale projection after a confirmed decision to prevent duplicate approval", () => {
    const { props } = renderView({
      decisionRequestId: pendingMembership.requestId,
      decisionSuccessMessage: "批准已由服务端确认。",
    });

    expect(
      screen.getByRole("button", { name: "批准申请", pressed: true }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交批准申请" })).toBeDisabled();
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(within(status).getByText("批准已由服务端确认。")).toBeVisible();
    expect(
      within(screen.getByRole("dialog", { name: "审批申请" })).queryByRole(
        "status",
      ),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭成功提示" }));
    expect(props.onDecisionSuccessDismiss).toHaveBeenCalledOnce();
  });

  it("does not let a previous request success disable a newly opened request", () => {
    renderView({
      decisionRequestId: "previous-request",
      decisionSuccessMessage: "批准已由服务端确认。",
    });

    expect(
      screen.getByRole("button", { name: "批准申请", pressed: true }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "提交批准申请" })).toBeEnabled();
  });

  it("keeps the polite page status after refetch removes the selected row and drawer", () => {
    const view = renderView({
      decisionSuccessMessage: "批准已由服务端确认。",
    });

    view.rerender(
      <AccessApprovalView
        {...view.props}
        membership={{
          rows: [],
          loading: false,
          fetching: false,
          error: null,
        }}
        decisionSuccessMessage="批准已由服务端确认。"
      />,
    );

    expect(screen.queryByRole("dialog", { name: "审批申请" })).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(
      "批准已由服务端确认。",
    );
    expect(screen.getByRole("button", { name: "关闭成功提示" })).toHaveFocus();
  });

  it("never renders a decision error alongside a confirmed success", () => {
    renderView({
      decisionError: domainError(409),
      decisionSuccessMessage: "批准已由服务端确认。",
    });

    expect(screen.getByRole("status")).toBeVisible();
    expect(screen.queryByText("申请状态已变化")).not.toBeInTheDocument();
    expect(screen.queryByText("ACCESS_409")).not.toBeInTheDocument();
  });

  it("keeps a real high-impact 403 and request ID as the decision failure result", () => {
    renderView({
      search: { ...defaultSearch, tab: "capability-requests" },
      decisionError: domainError(403),
      decisionRequestId: riskyCapability.requestId,
    });

    expect(screen.getByText("访问申请操作失败 403")).toBeVisible();
    expect(screen.getByText("ACCESS_403")).toBeVisible();
    expect(screen.getByText("request-403")).toBeVisible();
  });

  it.each([
    [409, "申请状态已变化"],
    [429, "审批请求频率受限"],
  ] as const)(
    "keeps HTTP %s problem details and recovery in the drawer",
    (status, title) => {
      renderView({ decisionError: domainError(status) });

      expect(screen.getByText(title)).toBeVisible();
      expect(screen.getByText(`ACCESS_${status}`)).toBeVisible();
      expect(screen.getByText(`request-${status}`)).toBeVisible();
      expect(
        screen.getByRole("button", { name: "重新加载申请" }),
      ).toBeEnabled();
    },
  );

  it("renders list-level 403 without a browser mock fallback", () => {
    renderView({
      membership: {
        rows: [],
        loading: false,
        fetching: false,
        error: domainError(403),
      },
    });

    expect(screen.getByText("访问申请操作失败 403")).toBeVisible();
    expect(screen.getByText("request-403")).toBeVisible();
    expect(
      screen.queryByText("参与当前项目的数据整理"),
    ).not.toBeInTheDocument();
  });

  it("closes the non-modal drawer with Escape and restores focus to the selected request", () => {
    const { props } = renderView();
    const selected = screen.getByRole("button", { name: /contractor-17/u });
    fireEvent.keyDown(window, { key: "Escape" });

    expect(props.onSearchChange).toHaveBeenCalledWith({ drawer: "closed" });
    expect(selected).toHaveFocus();
  });

  it("exposes only project membership and capability request lists", () => {
    renderView();

    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.queryByRole("tab", { name: "用户" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "项目成员" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "权限模板" }),
    ).not.toBeInTheDocument();
  });
});
