// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import type { ManagedAccount } from "./contracts";
import type { AccessSearch } from "./query-codec";
import {
  UserManagementPanel,
  type UserManagementPanelProps,
} from "./UserManagementPanel";

const account: ManagedAccount = {
  principal_id: "96d2afd5-33d1-4e63-b22a-9d296cd24c18",
  username: "robot-operator",
  display_name: "机器人操作员",
  state: "ACTIVE",
  platform_role: "USER",
  recovery_email_configured: true,
  recovery_email_hint: "op******@example.com",
  active_session_count: 2,
  created_at: "2026-08-20T08:00:00Z",
  updated_at: "2026-08-21T08:00:00Z",
  password_changed_at: "2026-08-20T08:00:00Z",
  deleted_at: null,
  revision: 1,
  etag: '"v1"',
};

const search: AccessSearch = {
  tab: "users",
  status: "ALL",
  accountState: "ALL",
  accountRole: "ALL",
  order: "recent",
  page: 1,
  pageSize: 10,
  drawer: "closed",
};

function props(
  overrides: Partial<UserManagementPanelProps> = {},
): UserManagementPanelProps {
  return {
    search,
    page: { items: [account], page: 1, page_size: 10, total: 1 },
    loading: false,
    fetching: false,
    error: null,
    mutationPending: false,
    mutationError: null,
    canManage: true,
    canUnlock: true,
    currentPrincipalId: "different-admin",
    onSearchChange: vi.fn(),
    onRefresh: vi.fn(),
    onCreate: vi.fn().mockResolvedValue(undefined),
    onStatusChange: vi.fn().mockResolvedValue(undefined),
    onRoleChange: vi.fn().mockResolvedValue(undefined),
    onResetPassword: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onUnlock: vi.fn().mockResolvedValue(undefined),
    onDismissStatus: vi.fn(),
    ...overrides,
  };
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
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
});

afterEach(cleanup);

describe("P18 platform user management", () => {
  it("shows password-free account facts and a reset flow that never asks for the old password", async () => {
    const user = userEvent.setup();
    render(<UserManagementPanel {...props()} />);

    expect(screen.getByText("机器人操作员")).toBeVisible();
    expect(screen.getByText("op******@example.com")).toBeVisible();
    expect(screen.getAllByText("活动会话")[0]).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "管理用户 机器人操作员" }),
    );
    await user.click(await screen.findByText("重置密码"));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/无需原密码.*所有已登录会话会立即失效/u),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("新密码")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(within(dialog).queryByLabelText("原密码")).not.toBeInTheDocument();
  });

  it("requires the exact username before a soft delete can be confirmed", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<UserManagementPanel {...props({ onDelete })} />);

    await user.click(
      screen.getByRole("button", { name: "管理用户 机器人操作员" }),
    );
    await user.click(await screen.findByText("删除用户"));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认删除" });
    expect(confirm).toBeDisabled();

    await user.type(
      within(dialog).getByLabelText(/输入用户名/u),
      "robot-operator",
    );
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(account));
  });

  it("does not offer password-reset or destructive shortcuts for the current administrator", async () => {
    const user = userEvent.setup();
    render(
      <UserManagementPanel
        {...props({ currentPrincipalId: account.principal_id })}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "管理用户 机器人操作员" }),
    );

    expect(
      await screen.findByRole("menuitem", {
        name: "重置密码（请前往账户设置）",
      }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: "禁用用户" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("menuitem", { name: "删除用户" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("keeps a failed dangerous action open and shows the server error inside its dialog", async () => {
    const failure = createDomainError({
      code: "VERSION_CONFLICT",
      problemCode: "ACCOUNT_REVISION_CONFLICT",
      message: "用户已被其他管理员更新，请刷新后重试。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: "request-account-conflict",
      retryable: false,
      httpStatus: 409,
    });
    const onDelete = vi.fn().mockRejectedValue(failure);
    const user = userEvent.setup();
    const view = render(<UserManagementPanel {...props({ onDelete })} />);

    await user.click(
      screen.getByRole("button", { name: "管理用户 机器人操作员" }),
    );
    await user.click(await screen.findByText("删除用户"));
    let dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText(/输入用户名/u),
      "robot-operator",
    );
    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledOnce());

    view.rerender(
      <UserManagementPanel {...props({ onDelete, mutationError: failure })} />,
    );
    dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("用户已被其他管理员更新，请刷新后重试。"),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("button", { name: "确认删除" }),
    ).toBeVisible();
  });

  it("lets a dedicated security manager clear a temporary lock without account mutation rights", async () => {
    const onUnlock = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <UserManagementPanel
        {...props({ canManage: false, canUnlock: true, onUnlock })}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "管理用户 机器人操作员" }),
    );
    expect(screen.getByRole("menuitem", { name: "禁用用户" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await user.click(screen.getByRole("menuitem", { name: "解除临时锁定" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("不会启用已禁用或已删除的账号");
    await user.click(within(dialog).getByRole("button", { name: "确认解锁" }));

    await waitFor(() => expect(onUnlock).toHaveBeenCalledWith(account));
  });
});
