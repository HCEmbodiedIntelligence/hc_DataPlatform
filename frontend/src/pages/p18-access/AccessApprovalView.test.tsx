// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
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
import type { AccessRequestRow, AccessRequestStatus } from "./contracts";
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
  updatedAt: "2026-08-19T01:00:00Z",
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
const ordinaryCapability: AccessRequestRow = {
  ...riskyCapability,
  requestId: "capability-ordinary",
  requesterId: "internal-10",
  capabilityKeys: ["datasets.read", "custom.unknown"],
};

const defaultSearch: AccessSearch = {
  tab: "membership-requests",
  status: "ALL",
  accountState: "ALL",
  accountRole: "ALL",
  order: "recent",
  page: 1,
  pageSize: 10,
  drawer: "closed",
};

function openedSearch(
  row: AccessRequestRow,
  overrides: Partial<AccessSearch> = {},
): AccessSearch {
  return {
    ...defaultSearch,
    tab:
      row.kind === "membership" ? "membership-requests" : "capability-requests",
    requestId: row.requestId,
    drawer: "open",
    ...overrides,
  };
}

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
      rows: [riskyCapability, ordinaryCapability],
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
  const getComputedStyle = window.getComputedStyle.bind(window);
  Object.defineProperty(window, "getComputedStyle", {
    configurable: true,
    value: (element: Element) => getComputedStyle(element),
  });
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

describe("P18 access approval information architecture", () => {
  it("starts with a full request list and no selected request or drawer", () => {
    renderView();

    expect(screen.getByRole("table", { name: "申请列表" })).toBeVisible();
    expect(screen.getByText("已加载 2 条")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/注册账户不进入审批队列/u),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("目标项目")).not.toBeInTheDocument();
    expect(screen.queryByText("申请类型")).not.toBeInTheDocument();
  });

  it("opens the explicitly requested row instead of selecting the first row", async () => {
    const user = userEvent.setup();
    const view = renderView();

    await user.click(
      screen.getByRole("button", { name: "查看 internal-8 的申请" }),
    );
    expect(view.props.onSearchChange).toHaveBeenCalledWith({
      requestId: approvedMembership.requestId,
      drawer: "open",
    });

    view.rerender(
      <AccessApprovalView
        {...view.props}
        search={openedSearch(approvedMembership)}
        decisionRequestId={approvedMembership.requestId}
      />,
    );
    const drawer = screen.getByRole("dialog");
    expect(
      within(drawer).getByRole("heading", { name: "项目加入申请" }),
    ).toBeVisible();
    expect(within(drawer).getByText("internal-8")).toBeVisible();
    expect(within(drawer).queryByText("contractor-17")).not.toBeInTheDocument();
  });

  it("restores a legal deep link and safely closes an unknown request", async () => {
    const valid = renderView({ search: openedSearch(approvedMembership) });
    expect(screen.getByRole("dialog")).toBeVisible();
    valid.unmount();

    const invalid = renderView({
      search: {
        ...defaultSearch,
        requestId: "missing-request",
        drawer: "open",
      },
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(invalid.props.onSearchChange).toHaveBeenCalledWith({
        requestId: undefined,
        drawer: "closed",
      }),
    );
  });

  it("lets Escape and the close button close the modal drawer and restore row focus", async () => {
    const user = userEvent.setup();
    const escapeView = renderView({ search: openedSearch(pendingMembership) });
    const trigger = screen.getByRole("button", {
      name: "查看 contractor-17 的申请",
    });

    fireEvent.keyDown(document, { key: "Escape", keyCode: 27 });
    expect(escapeView.props.onSearchChange).toHaveBeenCalledWith({
      requestId: undefined,
      drawer: "closed",
    });
    expect(trigger).toHaveFocus();
    escapeView.unmount();

    const closeView = renderView({ search: openedSearch(pendingMembership) });
    const closeTrigger = screen.getByRole("button", {
      name: "查看 contractor-17 的申请",
    });
    await user.click(screen.getByRole("button", { name: "关闭申请详情" }));
    expect(closeView.props.onSearchChange).toHaveBeenCalledWith({
      requestId: undefined,
      drawer: "closed",
    });
    expect(closeTrigger).toHaveFocus();
  });

  it("closes details when the tab, filter, search or pagination context changes", async () => {
    const user = userEvent.setup();
    const manyRows = Array.from({ length: 11 }, (_, index) => ({
      ...pendingMembership,
      requestId: `membership-${index}`,
      requesterId: `requester-${index}`,
      createdAt: `2026-08-${String(index + 1).padStart(2, "0")}T01:00:00Z`,
    }));
    const view = renderView({
      search: openedSearch(manyRows[0]!),
      membership: {
        rows: manyRows,
        loading: false,
        fetching: false,
        error: null,
      },
    });

    await user.click(screen.getByRole("tab", { name: /权限申请/u }));
    expect(view.props.onSearchChange).toHaveBeenCalledWith(
      expect.objectContaining({
        tab: "capability-requests",
        requestId: undefined,
        drawer: "closed",
      }),
    );

    await user.click(screen.getByLabelText("状态"));
    await user.click(
      await screen.findByText("已批准", {
        selector: ".ant-select-item-option-content",
      }),
    );
    expect(view.props.onSearchChange).toHaveBeenCalledWith({
      status: "APPROVED",
      page: 1,
      requestId: undefined,
      drawer: "closed",
    });

    await user.type(screen.getByLabelText("搜索申请"), "robot");
    expect(view.props.onSearchChange).toHaveBeenLastCalledWith({
      q: "t",
      page: 1,
      requestId: undefined,
      drawer: "closed",
    });

    await user.click(screen.getByRole("listitem", { name: "2" }));
    expect(view.props.onSearchChange).toHaveBeenCalledWith({
      page: 2,
      pageSize: 10,
      requestId: undefined,
      drawer: "closed",
    });
  });

  it("shows pagination only when the loaded result exceeds pageSize", () => {
    const small = renderView();
    expect(
      screen.queryByRole("list", { name: /分页/u }),
    ).not.toBeInTheDocument();
    small.unmount();

    renderView({
      membership: {
        rows: Array.from({ length: 11 }, (_, index) => ({
          ...pendingMembership,
          requestId: `membership-${index}`,
        })),
        loading: false,
        fetching: false,
        error: null,
      },
    });
    expect(screen.getByRole("listitem", { name: "2" })).toBeVisible();
    expect(screen.getByText("11 条筛选结果")).toBeVisible();
  });

  it("keeps the confirmed manager, applicant and read-only decision matrix", () => {
    const manager = renderView({ search: openedSearch(pendingMembership) });
    expect(screen.getByRole("button", { name: "批准申请" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "拒绝申请" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "撤回申请" })).toBeNull();
    expect(screen.queryByRole("button", { name: /提交/u })).toBeNull();
    manager.unmount();

    const applicant = renderView({
      search: openedSearch(pendingMembership),
      canManage: false,
      principalId: pendingMembership.requesterId,
    });
    expect(screen.queryByRole("button", { name: "撤回申请" })).toBeNull();
    expect(screen.queryByRole("button", { name: "批准申请" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝申请" })).toBeNull();
    expect(screen.getByText(/此申请仅供查看/u)).toBeVisible();
    applicant.unmount();

    renderView({
      search: openedSearch(pendingMembership),
      canManage: false,
      principalId: "read-only-user",
    });
    expect(
      screen.getByText("当前身份或申请状态没有可执行操作，此申请仅供查看。"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /批准|拒绝|撤回/u }),
    ).toBeNull();
    expect(screen.queryByRole("alert", { name: /只读/u })).toBeNull();
  });

  it.each([
    ["APPROVED", ["撤销授权"]],
    ["REJECTED", []],
    ["WITHDRAWN", []],
    ["REVOKED", []],
  ] as const)(
    "renders only allowed manager actions for %s",
    (status, labels) => {
      const row: AccessRequestRow = {
        ...approvedMembership,
        requestId: `membership-${status.toLowerCase()}`,
        status: status as AccessRequestStatus,
      };
      renderView({
        search: openedSearch(row),
        membership: {
          rows: [row],
          loading: false,
          fetching: false,
          error: null,
        },
      });

      for (const label of labels) {
        expect(screen.getByRole("button", { name: label })).toBeEnabled();
      }
      for (const unavailable of ["批准申请", "拒绝申请", "撤回申请"]) {
        expect(screen.queryByRole("button", { name: unavailable })).toBeNull();
      }
      if (labels.length === 0)
        expect(screen.getByText(/此申请仅供查看/u)).toBeVisible();
    },
  );

  it("does not preselect a decision and allows approve without a reason", async () => {
    const user = userEvent.setup();
    const view = renderView({ search: openedSearch(pendingMembership) });

    expect(screen.getByRole("button", { name: "批准申请" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryByRole("button", { name: "提交批准申请" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "批准申请" }));
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));
    expect(view.props.onDecision).toHaveBeenCalledWith({
      kind: "membership",
      action: "approve",
      projectId: "project-a",
      requestId: "membership-pending",
      reason: null,
    });
  });

  it.each([
    ["reject", pendingMembership, true],
    ["revoke", approvedMembership, true],
  ] as const)(
    "requires an inline reason for %s",
    async (action, row, canManage) => {
      const user = userEvent.setup();
      const view = renderView({
        search: openedSearch(row),
        canManage,
        principalId: "admin-1",
        decisionRequestId: row.requestId,
      });
      const label =
        action === "reject"
          ? "拒绝申请"
          : "撤销授权";

      await user.click(screen.getByRole("button", { name: label }));
      await user.click(screen.getByRole("button", { name: `提交${label}` }));
      expect(screen.getByRole("alert")).toHaveTextContent(
        "拒绝或撤销必须填写原因。",
      );
      expect(
        screen.getByRole("textbox", { name: /处理原因/u }),
      ).toHaveAttribute("aria-invalid", "true");
      expect(view.props.onDecision).not.toHaveBeenCalled();
    },
  );

  it("confirms the real membership revoke cascade before submitting", async () => {
    const user = userEvent.setup();
    const view = renderView({
      search: openedSearch(approvedMembership),
      decisionRequestId: approvedMembership.requestId,
    });

    await user.click(screen.getByRole("button", { name: "撤销授权" }));
    await user.type(
      screen.getByRole("textbox", { name: /处理原因/u }),
      "成员离组",
    );
    await user.click(screen.getByRole("button", { name: "提交撤销授权" }));
    const confirmation = screen
      .getByText("确认撤销项目成员关系")
      .closest<HTMLElement>("[role='dialog']");
    expect(confirmation).not.toBeNull();
    expect(confirmation).toHaveTextContent(
      "停用该用户在当前项目的成员关系，并同时停用该项目内相关 capability 授权",
    );
    expect(view.props.onDecision).not.toHaveBeenCalled();

    await user.click(
      within(confirmation!).getByRole("button", {
        name: "撤销成员及相关权限",
      }),
    );
    expect(view.props.onDecision).toHaveBeenCalledWith({
      kind: "membership",
      action: "revoke",
      projectId: "project-a",
      requestId: "membership-approved",
      reason: "成员离组",
    });
  });

  it("disables repeat actions and shows loading during a mutation", async () => {
    const user = userEvent.setup();
    const view = renderView({ search: openedSearch(pendingMembership) });
    await user.click(screen.getByRole("button", { name: "批准申请" }));

    view.rerender(
      <AccessApprovalView
        {...view.props}
        search={openedSearch(pendingMembership)}
        decisionPending
      />,
    );
    expect(screen.getByRole("button", { name: "批准申请" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝申请" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /提交中/u })).toBeDisabled();
  });

  it("shows progressive real details and no invented contract placeholders", () => {
    renderView({ search: openedSearch(riskyCapability) });

    expect(screen.queryByText("请求包含高影响能力")).not.toBeInTheDocument();
    expect(screen.getByRole("note", { name: "影响提示" })).toBeVisible();
    expect(screen.getByText("数据集发布")).toBeVisible();
    expect(screen.getByText("datasets.publish")).toBeVisible();
    expect(screen.queryByText("处理记录")).not.toBeInTheDocument();
    for (const forbiddenCopy of [
      "权限模板",
      "服务端风险等级",
      "正式合同未提供 region",
      "长期有效",
      "风险上下文",
    ]) {
      expect(screen.queryByText(new RegExp(forbiddenCopy, "u"))).toBeNull();
    }
  });

  it("shows an unknown capability as its original key without an empty risk message", () => {
    renderView({ search: openedSearch(ordinaryCapability) });

    expect(screen.getByText("数据集只读")).toBeVisible();
    expect(screen.getByText("datasets.read")).toBeVisible();
    expect(screen.getByText("custom.unknown")).toBeVisible();
    expect(screen.queryByRole("note", { name: "影响提示" })).toBeNull();
    expect(screen.queryByText(/未识别风险|不是服务端风险评级/u)).toBeNull();
  });

  it("shows handled facts only for processed requests", () => {
    renderView({ search: openedSearch(approvedMembership) });

    expect(screen.getByText("处理记录")).toBeVisible();
    expect(screen.getByText("admin-1")).toBeVisible();
    expect(screen.getByText("项目负责人确认")).toBeVisible();
    expect(screen.getByText("技术信息")).toBeVisible();
    expect(screen.queryByText("申请 ID")).not.toBeVisible();
  });
});

describe("P18 decision feedback and recovery", () => {
  it("keeps a polite confirmed success and disables the stale projection", () => {
    renderView({
      search: openedSearch(pendingMembership),
      decisionSuccessMessage: "批准申请已由服务端确认。",
    });

    expect(screen.getByRole("button", { name: "批准申请" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "提交批准申请" })).toBeNull();
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(status).toHaveTextContent("批准申请已由服务端确认。");
  });

  it.each([
    [403, "当前权限无法完成操作"],
    [409, "申请状态已变化"],
    [429, "审批请求频率受限"],
  ] as const)(
    "keeps HTTP %s details, request ID and retry",
    (status, title) => {
      renderView({
        search: openedSearch(pendingMembership),
        decisionError: domainError(status),
        decisionRequestId: pendingMembership.requestId,
      });

      expect(screen.getByText(title)).toBeVisible();
      expect(screen.getByText(`ACCESS_${status}`)).toBeVisible();
      expect(screen.getByText(`request-${status}`)).toBeVisible();
      expect(
        screen.getByRole("button", { name: "重新加载申请" }),
      ).toBeEnabled();
      expect(screen.queryByRole("status")).toBeNull();
    },
  );

  it("renders a list-level 403 without fallback request data", () => {
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
    expect(screen.queryByText("参与当前项目的数据整理")).toBeNull();
  });
});
