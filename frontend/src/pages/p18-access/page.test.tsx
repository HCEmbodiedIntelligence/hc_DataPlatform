// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { makeScopeKey } from "../../entities/scope";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import type { MembershipRequest } from "./contracts";
import AccessPage from "./page";

vi.mock("../../shared/api/http-client", async (original) => ({
  ...(await original<typeof import("../../shared/api/http-client")>()),
  request: vi.fn(),
}));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "org-p18",
  projectId: "project-a",
  regionCode: "cn-east-01",
} as const;
const pendingMembership: MembershipRequest = {
  request_id: "membership-pending",
  organization_id: scope.organizationId,
  project_id: scope.projectId,
  requester_id: "contractor-17",
  status: "PENDING",
  reason: "参与当前项目的数据整理",
  created_at: "2026-08-18T01:00:00Z",
  updated_at: "2026-08-18T01:00:00Z",
  revision: 1,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/access"]}>
        <AccessPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  requestMock.mockReset();
  const getComputedStyle = window.getComputedStyle.bind(window);
  Object.defineProperty(window, "getComputedStyle", {
    configurable: true,
    value: (element: Element) => getComputedStyle(element),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
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
  useShellStore.setState({
    principal: {
      actorId: "admin-1",
      displayName: "项目管理员",
      roleIds: ["PROJECT_ADMIN"],
    },
    sessionToken: "p18-page-test-token",
    scope,
    scopeKey: makeScopeKey(scope),
    scopeChanging: false,
    authorization: {
      scopeKey: makeScopeKey(scope),
      roleVersion: "p18-role-v1",
      capabilities: ["access.read", "access.manage"],
      fetchedAt: "2026-08-18T01:00:00Z",
    },
    authorizationLoading: false,
    authorizationFailed: false,
    platformCapabilities: [],
    sessionScopes: [], sessionOrganizations: [],
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("P18 page-owned decision feedback", () => {
  it("does not issue account or approval requests without either read grant", () => {
    useShellStore.setState({
      scope: null,
      authorization: null,
      platformCapabilities: [],
    });

    renderPage();

    expect(screen.getByRole("alert", { name: "账户与权限" })).toHaveAttribute(
      "data-page-state",
      "forbidden",
    );
    expect(
      screen.getByText("当前身份没有项目审批读取权限或平台账号读取权限。"),
    ).toBeVisible();
    expect(requestMock).not.toHaveBeenCalled();
  });

  it("opens only global user management for a platform administrator without project scope", async () => {
    requestMock.mockImplementation(async options => options.path === "/account/management-directory"
      ? { organizations: [], projects: [] }
      : { items: [], page: 1, page_size: 10, total: 0 });
    useShellStore.setState({
      scope: null,
      authorization: null,
      platformCapabilities: [
        "platform.account.read",
        "platform.account.manage",
      ],
    });

    renderPage();

    await screen.findByRole("region", { name: "平台用户管理" });
    expect(screen.getByRole("tab", { name: "用户管理" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.queryByRole("tab", { name: /项目加入申请/u }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /权限申请/u }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/自助注册账户.*不进入项目审批队列/u)).toBeVisible();
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "GET",
        path: "/platform/accounts",
        scopeMode: "session",
      }),
    );
  });

  it("loads the project queue without automatically opening the first request", async () => {
    requestMock.mockImplementation(async (options) => {
      if (options.path === "/account/management-directory") return { organizations: [], projects: [] };
      if (options.path.endsWith("membership-requests"))
        return { items: [pendingMembership] };
      if (options.path.endsWith("capability-requests")) return { items: [] };
      throw new Error(`Unexpected request: ${options.method} ${options.path}`);
    });

    renderPage();

    await screen.findByRole("button", {
      name: "查看 contractor-17 的申请",
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps a polite dismissible success after the successful mutation refetch removes the selected row and drawer", async () => {
    const user = userEvent.setup();
    let membershipRows: MembershipRequest[] = [pendingMembership];
    requestMock.mockImplementation(async (options) => {
      if (options.path === "/account/management-directory") return { organizations: [], projects: [] };
      if (
        options.method === "GET" &&
        options.path.endsWith("membership-requests")
      )
        return { items: membershipRows };
      if (
        options.method === "GET" &&
        options.path.endsWith("capability-requests")
      )
        return { items: [] };
      if (options.method === "POST") {
        membershipRows = [];
        return {
          ...pendingMembership,
          status: "APPROVED",
          decided_by: "admin-1",
          revision: 2,
        };
      }
      throw new Error(`Unexpected request: ${options.method} ${options.path}`);
    });

    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "查看 contractor-17 的申请",
      }),
    );
    await user.click(screen.getByRole("button", { name: "批准申请" }));
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("批准申请已由服务端确认。");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(
      screen.queryByRole("dialog", { name: "审批申请" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看 contractor-17 的申请" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭成功提示" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "关闭成功提示" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("clears an old success when the tab/search context or project scope changes", async () => {
    const user = userEvent.setup();
    let membershipRows: MembershipRequest[] = [pendingMembership];
    requestMock.mockImplementation(async (options) => {
      if (options.path === "/account/management-directory") return { organizations: [], projects: [] };
      if (
        options.method === "GET" &&
        options.path.endsWith("membership-requests")
      )
        return { items: membershipRows };
      if (
        options.method === "GET" &&
        options.path.endsWith("capability-requests")
      )
        return { items: [] };
      membershipRows = [];
      return {
        ...pendingMembership,
        status: "APPROVED",
        decided_by: "admin-1",
        revision: 2,
      };
    });

    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "查看 contractor-17 的申请",
      }),
    );
    await user.click(screen.getByRole("button", { name: "批准申请" }));
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));
    await screen.findByRole("status");

    await user.click(screen.getByRole("tab", { name: /权限申请/u }));
    expect(
      screen.queryByText("批准申请已由服务端确认。"),
    ).not.toBeInTheDocument();

    membershipRows = [pendingMembership];
    await user.click(screen.getByRole("tab", { name: /项目加入申请/u }));
    await user.click(screen.getByRole("button", { name: "刷新" }));
    await user.click(
      await screen.findByRole("button", {
        name: "查看 contractor-17 的申请",
      }),
    );
    await user.click(screen.getByRole("button", { name: "批准申请" }));
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));
    await screen.findByRole("status");

    const nextScope = { ...scope, projectId: "project-b" };
    await act(async () => {
      useShellStore.setState({
        scope: nextScope,
        scopeKey: makeScopeKey(nextScope),
      });
    });
    await waitFor(() =>
      expect(
        screen.queryByText("批准申请已由服务端确认。"),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows only Problem Details after a failed decision, never success", async () => {
    const user = userEvent.setup();
    const failure = createDomainError({
      code: "VERSION_CONFLICT",
      problemCode: "ACCESS_409",
      message: "申请状态已由其他管理员更新。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: "request-p18-409",
      retryable: false,
      httpStatus: 409,
    });
    requestMock.mockImplementation(async (options) => {
      if (options.path === "/account/management-directory") return { organizations: [], projects: [] };
      if (
        options.method === "GET" &&
        options.path.endsWith("membership-requests")
      )
        return { items: [pendingMembership] };
      if (
        options.method === "GET" &&
        options.path.endsWith("capability-requests")
      )
        return { items: [] };
      throw failure;
    });

    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "查看 contractor-17 的申请",
      }),
    );
    await user.click(screen.getByRole("button", { name: "批准申请" }));
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));

    expect(await screen.findByText("申请状态已变化")).toBeVisible();
    expect(screen.getByText("ACCESS_409")).toBeVisible();
    expect(screen.getByText("request-p18-409")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("closes an open request when the Shell project scope changes", async () => {
    requestMock.mockImplementation(async (options) => {
      if (options.path === "/account/management-directory") return { organizations: [], projects: [] };
      if (options.path.endsWith("membership-requests"))
        return { items: [pendingMembership] };
      if (options.path.endsWith("capability-requests")) return { items: [] };
      throw new Error(`Unexpected request: ${options.method} ${options.path}`);
    });
    const user = userEvent.setup();

    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "查看 contractor-17 的申请",
      }),
    );
    expect(screen.getByRole("dialog")).toBeVisible();

    const nextScope = { ...scope, projectId: "project-b" };
    await act(async () => {
      useShellStore.setState({
        scope: nextScope,
        scopeKey: makeScopeKey(nextScope),
      });
    });

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});

describe("账户与权限管理树", () => {
  it.each([null, scope])("shows both management tabs for a platform administrator with scope %j", async current => {
    const user=userEvent.setup();
    useShellStore.setState({ scope: current, authorization:null, platformCapabilities:["platform.admin"] });
    const member={principal_id:"person-1",username:"member.one",administrator:false};
    requestMock.mockImplementation(async opts=>{
      if(opts.path==="/account/management-directory")return {organizations:[{organization_id:"01",organization_name:"杭州",can_manage:true}],
        projects:[{organization_id:"01",project_id:"open",project_name:"机器人项目"}]};
      if(opts.path.startsWith("/account/managed-members/person-1?"))return {...member,display_name:"测试成员",status:"ACTIVE",
        created_at:"2026-11-24T00:00:00Z",assigned_roles:["role.observer"],platform_administrator:false,organization_administrator:false,
        assigned_permissions:[],assignable_permissions:["training.create","teleoperation.connect"],
        effective_permissions:["dataset.read"],approved_grants:[],can_set_roles:true,can_remove:true,can_change_administrator:true};
      if(opts.path.includes("unassigned_only=true"))return [{...member,username:"waiting.member",principal_id:"waiting-1"}];
      if(opts.path.startsWith("/account/managed-members?"))return [member];
      if(opts.path.startsWith("/account/management-inbox"))return [];
      return {items:[],total:0,page:1,page_size:10};
    });
    renderPage();
    const organization=await screen.findByRole("tab",{name:"组织管理"});
    await user.click(organization);
    expect(organization).toHaveAttribute("aria-selected","true");
    const orgTree = await screen.findByRole("tree");
    const waiting = await within(orgTree).findByText("waiting.member");
    const projectLabel = within(orgTree).getByText("机器人项目");
    expect(projectLabel.compareDocumentPosition(waiting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(orgTree).queryByText("组织成员")).toBeNull();
    expect(within(orgTree).queryByText("member.one")).toBeNull();
    const projects=screen.getByRole("tab",{name:"项目管理"});
    await user.click(projects);
    const tree=await screen.findByRole("tree");
    const name=await within(tree).findByText("member.one");
    await user.click(name);
    const dialog=await screen.findByRole("dialog",{name:"member.one · 成员详情"});
    expect(await within(dialog).findByText("测试成员")).toBeVisible();
    expect(within(dialog).getByText("数据集只读")).toBeVisible();
    expect(within(dialog).getByRole("button",{name:"移出项目"})).toBeVisible();
    await user.click(within(dialog).getByRole("combobox", {name:"选择专项权限"}));
    await user.click(await screen.findByText("远程遥操作 · 连接指定机器人", {selector: ".ant-select-item-option-content"}));
    const add = within(dialog).getByRole("button", {name:"加入列表"});
    expect(add).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox", {name:"机器人 ID"}), "robot-42");
    await user.click(add);
    await user.click(within(dialog).getByRole("button", {name:"保存专项权限"}));
    await user.type(await screen.findByRole("textbox", {name:"操作原因"}), "设备验收");
    await user.click(screen.getByRole("button", {name:/^确\s*认$/}));
    await waitFor(() => expect(requestMock).toHaveBeenCalledWith(expect.objectContaining({
      method:"PUT", path:"/account/managed-members/person-1/permissions",
      body:{organization_id:"01", project_id:"open", permissions:["teleoperation.connect/robot-42"],
        expected_permissions:[], reason:"设备验收"},
    })));
    expect(requestMock).toHaveBeenCalledWith(expect.objectContaining({path:"/account/managed-members/person-1?organization_id=01&project_id=open"}));
  });

  it("shows the project manager only the project management tab before selecting a project", async () => {
    useShellStore.setState({scope:null,authorization:null,platformCapabilities:[],
      sessionScopes:[{organizationId:"01",projectId:"alpha",capabilities:["access.manage"],projectWide:true,regionCodes:[]}]});
    requestMock.mockImplementation(async opts=> opts.path==="/account/management-directory" ? {
      organizations:[{organization_id:"01",organization_name:"杭州",can_manage:false}],projects:[{organization_id:"01",project_id:"alpha",project_name:"生产项目"}],
    } : []);
    renderPage();
    expect(await screen.findByRole("tab",{name:"项目管理"})).toHaveAttribute("aria-selected","true");
    expect(screen.queryByRole("tab",{name:"组织管理"})).not.toBeInTheDocument();
    await within(await screen.findByRole("tree")).findByText("生产项目");
    expect(requestMock.mock.calls.some(([o])=>o.path.startsWith("/account/managed-members?") && !o.path.includes("project_id="))).toBe(false);
  });
});
