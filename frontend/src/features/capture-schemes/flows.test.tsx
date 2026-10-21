// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import { Component as Library } from "../../pages/p24-capture-schemes/page";
import { CollectionTaskDrawer } from "../../pages/p20-collection-tasks/components/CollectionTaskDrawer";
import { TaskPlanSelector, type TaskPlanSelection } from "./TaskPlanSelector";
import { TaskPlanManager } from "./TaskPlanManager";
import { taskPlanRequest } from "./api";
import { RobotSchemeAssociations } from "./RobotSchemeAssociations";
import {
  fixtureDetail,
  fixtureDocument,
  fixtureLink,
  fixtureVersion,
} from "./fixtures.test-support";
import type {
  SchemeDetail,
  SchemeLink,
  SchemeDraft,
  TaskPlan,
} from "./contracts";
import { draftKey, saveDraft } from "./navigation";
import { versionSchema } from "./contracts";
const flags = vi.hoisted(() => ({ manage: true, taskManage: true }));
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useOrganizationCapabilities: () => ({
    has: (cap: string) => !cap.endsWith("manage") || flags.manage,
  }),
  useCapabilities: () => ({
    has: (cap: string) => cap !== "upload.manage" || flags.taskManage,
  }),
}));
vi.mock("../../shared/scope/current-organization", () => ({
  useCurrentOrganizationId: () => "org",
}));
vi.mock("../robot-models/api/hooks", () => ({
  useScopedRobotModels: () => ({ data: { items: [] }, isFetching: false }),
}));
vi.mock("../robots/api/hooks", () => ({
  useScopedRobots: () => ({
    data: {
      items: [
        {
          id: "r1",
          displayName: "设备一",
          serialNo: "one",
          lifecycle: "ACTIVE",
        },
        {
          id: "r2",
          displayName: "设备二",
          serialNo: "two",
          lifecycle: "ACTIVE",
        },
      ],
    },
    isFetching: false,
  }),
}));
// Samples in these workflow tests are server-fixture selections, not real storage decoding.
vi.mock("./SamplePicker", () => ({
  SamplePicker: ({ onChange }: { onChange: (s: unknown) => void }) => (
    <button
      type="button"
      onClick={() =>
        onChange({
          project_id: "project",
          region_code: "global",
          raw_source_id: "sample",
          source_path: "meta/info.json",
          episode_index: 0,
          source_format: "LEROBOT_V3",
        })
      }
    >
      选择测试样本
    </button>
  ),
}));
let state: SchemeDetail;
let links: Record<string, SchemeLink[]>;
let client: QueryClient;
let taskPlan: TaskPlan;
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "global",
};
function mount(
  child: React.ReactNode,
  path = "/collection-tasks?drawer=create",
) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={child} />
          <Route path="/settings/capture-schemes/new" element={child} />
          <Route path="/settings/capture-schemes/:schemeId" element={child} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  sessionStorage.clear();
  flags.manage = true;
  flags.taskManage = true;
  state = fixtureDetail();
  links = { r1: [fixtureLink(3)], r2: [fixtureLink(7)] };
  taskPlan = {
    organization_id: "org",
    project_id: "project",
    region_code: "global",
    collection_task_id: "task",
    dataset_id: "dataset",
    revision: 2,
    scheme: { scheme_id: "shared", version: 1, content_sha256: "1".repeat(64) },
    output: fixtureVersion().output,
    locked_at: "2026-10-21T00:00:00Z",
    participants: [
      { robot_id: "r1", mapping_version: 3, mapping_sha256: "e".repeat(64) },
    ],
  };
  useShellStore.setState({
    sessionToken: "test-session",
    sessionScopes: [
      {
        ...scope,
        projectWide: false,
        regionCodes: ["global"],
        capabilities: [
          "robot.read",
          "robot.manage",
          "upload.read",
          "upload.manage",
        ],
      },
    ],
    scope,
    bootstrapLoaded: true,
    authorizationLoading: false,
    authorizationFailed: false,
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computed(element),
  );
  Element.prototype.scrollTo = vi.fn();
  vi.mocked(request).mockReset();
  vi.mocked(request).mockImplementation(async (options) => {
    if (options.path.includes("/capture-plan")) {
      if (options.method === "PUT") {
        const command = options.body as { scheme: TaskPlan["scheme"] };
        taskPlan = {
          ...taskPlan,
          scheme: command.scheme,
          revision: taskPlan.revision + 1,
          participants: [],
        };
      }
      return { plan: structuredClone(taskPlan) };
    }
    if (options.path.match(/\/versions\/\d+$/))
      return fixtureVersion(Number(options.path.split("/").at(-1)));
    const robot = options.path.match(/robots\/(r[12])\/capture-schemes/);
    if (robot) {
      const id = robot[1]!;
      if (options.path.endsWith(":remove")) {
        links[id] = links[id]!.map((l) => ({ ...l, active: false }));
        return { removed: true };
      }
      if (options.method === "PUT") {
        const command = options.body as {
          mapping: SchemeLink["mapping"]["mapping"];
          make_default: boolean;
        };
        links[id] = [
          {
            ...fixtureLink(9),
            status: "MAPPING_UNVERIFIED",
            mapping: {
              ...fixtureLink().mapping,
              mapping: command.mapping,
              mapping_version: 9,
            },
            is_default: command.make_default,
          },
        ];
      }
      if (options.path.endsWith(":validate"))
        links[id] = links[id]!.map((l) => ({ ...l, status: "READY" }));
      return { items: structuredClone(links[id]) };
    }
    if (options.path.endsWith("/references"))
      return {
        instances: [
          {
            robot_id: "r1",
            version: 1,
            mapping_version: 3,
            active: true,
            is_default: true,
          },
        ],
        tasks: [],
      };
    if (options.path.endsWith(":publish")) {
      const version = versionSchema.parse({
        ...fixtureVersion(2),
        ...state.draft,
        draft_revision: state.revision,
      });
      state.versions.unshift(version);
      state.latest_version = 2;
      return version;
    }
    if (options.path.endsWith(":validate")) {
      if (state.draft.output.frequency_hz === null) {
        state.draft.output.frequency_hz = 15;
        state.revision += 1;
      }
      state.validations = [
        {
          status: "PASSED",
          source_format: "LEROBOT_V3",
          revision: state.revision,
          draft_sha256: "c".repeat(64),
          sample_sha256: "d".repeat(64),
          parser_version: "lerobot-v3/1",
          presence: { "optional-camera": false },
          differences: [],
        },
      ];
      return structuredClone(state);
    }
    if (options.path.endsWith(":archive")) {
      state.archived = true;
      return structuredClone(state);
    }
    if (options.method === "PUT") {
      const { expected_revision, ...draft } = options.body as SchemeDraft;
      expect(expected_revision).toBe(state.revision);
      state = {
        ...state,
        draft,
        revision: expected_revision + 1,
        validations: [],
      };
      return structuredClone(state);
    }
    if (options.path.endsWith("/capture-schemes"))
      return { items: [{ ...state, instance_count: 2, task_count: 1 }] };
    return structuredClone(state);
  });
});
it("shows a locked scheme without participant enrollment or mapping controls", async () => {
  mount(<TaskPlanManager scope={scope} taskId="task" active />);
  await screen.findByText(/已经开始采集/);
  expect(screen.getByText(/使用同一采集方案的机器人均可/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "更换采集方案" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "追加参与实例" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("固定映射版本")).not.toBeInTheDocument();
  expect(
    vi.mocked(request).mock.calls.some(([o]) => o.path.includes("/robots/")),
  ).toBe(false);
});
it("permits explicit replacement only before the first recording and respects task permissions", async () => {
  taskPlan.locked_at = null;
  const view = mount(<TaskPlanManager scope={scope} taskId="task" active />);
  expect(
    await screen.findByRole("button", { name: "更换采集方案" }),
  ).toBeVisible();
  flags.taskManage = false;
  view.rerender(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TaskPlanManager scope={scope} taskId="task" active />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText(/尚未开始采集/);
  expect(
    screen.queryByRole("button", { name: "更换采集方案" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "追加参与实例" }),
  ).not.toBeInTheDocument();
});
it("rejects a task plan response from another project or task", async () => {
  taskPlan.project_id = "other-project";
  await expect(taskPlanRequest(scope, "task")).rejects.toMatchObject({
    code: "CONTRACT_MISMATCH",
  });
  taskPlan.project_id = "project";
  taskPlan.collection_task_id = "other-task";
  await expect(taskPlanRequest(scope, "task")).rejects.toMatchObject({
    code: "CONTRACT_MISMATCH",
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  vi.restoreAllMocks();
});
it("saves an empty frame rate, reads it during validation and publishes without changing v1", async () => {
  const user = userEvent.setup();
  state.draft.software = "";
  mount(<Library />, "/settings/capture-schemes/shared?edit=1");
  const input = await screen.findByLabelText("方案名称");
  expect(screen.queryByLabelText("采集软件")).not.toBeInTheDocument();
  await user.clear(input);
  await user.type(input, "新版方案");
  expect(
    screen.queryByRole("button", { name: "保存方案" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByText("数据定义"));
  const fps = screen.getByLabelText("实际原始帧率");
  await user.clear(fps);
  expect(fps).toHaveValue("");
  expect(
    fps.closest(".ant-form-item")?.querySelector(".ant-form-item-required"),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "保存方案" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByText("原始文件配置"));
  expect(
    screen.queryByRole("button", { name: "保存方案" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "下一步：确认与保存" }));
  expect(screen.getAllByRole("button", { name: "保存方案" })).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "保存方案" }));
  await waitFor(() => expect(state.revision).toBe(2));
  expect(state.draft.output.frequency_hz).toBeNull();
  await user.click(screen.getByText("确认与保存"));
  expect(screen.getByRole("button", { name: "发布新版本" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "选择测试样本" }));
  await user.click(screen.getByRole("button", { name: "验证当前格式" }));
  expect(await screen.findByText("未采集")).toBeVisible();
  expect(state.revision).toBe(3);
  expect(state.draft.output.frequency_hz).toBe(15);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "发布新版本" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "发布新版本" }));
  expect(await screen.findByText("已发布 v2 · 只读")).toBeVisible();
  expect(state.versions.find((v) => v.version === 1)?.name).toBe("X1 关节方案");
  expect(state.versions[0]?.output.frequency_hz).toBe(15);
  expect(state.versions[0]?.software).toBe("");
  expect(screen.queryByLabelText("方案名称")).not.toBeInTheDocument();
});
it("keeps read-only versions and management actions unavailable to a reader", async () => {
  flags.manage = false;
  mount(<Library />, "/settings/capture-schemes/shared?edit=1");
  expect(await screen.findByText("已发布 v1 · 只读")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "编辑方案" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "新建方案" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("方案名称")).not.toBeInTheDocument();
});
it("selects the latest published scheme without looking up any robots or mappings", async () => {
  state.versions.unshift(fixtureVersion(2));
  state.latest_version = 2;
  links = {};
  const changed = vi.fn();
  function Form() {
    const [value, set] = useState<TaskPlanSelection>({
      schemeId: "shared",
      versionKey: "",
    });
    return (
      <TaskPlanSelector
        organizationId="org"
        value={value}
        onSelectionChange={set}
        onChange={changed}
      />
    );
  }
  mount(<Form />);
  expect(await screen.findByText(/2 维状态/)).toBeVisible();
  await waitFor(() =>
    expect(changed).toHaveBeenLastCalledWith({
      expected_revision: 0,
      scheme: {
        scheme_id: "shared",
        version: 2,
        content_sha256: fixtureVersion(2).content_sha256,
      },
    }),
  );
  expect(screen.queryByLabelText("参与实例")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("任务采集方案版本")).not.toBeInTheDocument();
  expect(
    vi.mocked(request).mock.calls.some(([o]) => o.path.includes("/robots/")),
  ).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "更换版本" }));
  fireEvent.mouseDown(screen.getByLabelText("任务采集方案版本"));
  await userEvent.click(await screen.findByText("v1"));
  await waitFor(() =>
    expect(changed).toHaveBeenLastCalledWith({
      expected_revision: 0,
      scheme: {
        scheme_id: "shared",
        version: 1,
        content_sha256: fixtureVersion(1).content_sha256,
      },
    }),
  );
});
it.each(["archived", "unpublished"])(
  "rejects a restored %s scheme without inventing a task plan",
  async (kind) => {
    if (kind === "archived") state.archived = true;
    else {
      state.latest_version = null;
      state.versions = [];
    }
    const changed = vi.fn();
    mount(
      <TaskPlanSelector
        organizationId="org"
        value={{ schemeId: "shared", versionKey: "shared:1:" + "1".repeat(64) }}
        onSelectionChange={vi.fn()}
        onChange={changed}
      />,
    );
    expect(await screen.findByText(/暂无已发布的采集方案/)).toBeVisible();
    expect(changed).toHaveBeenLastCalledWith(null);
  },
);
it("keeps a restored version fixed when a newer version is published", async () => {
  state.versions.unshift(fixtureVersion(2));
  state.latest_version = 2;
  const changed = vi.fn();
  const selectionChanged = vi.fn();
  mount(
    <TaskPlanSelector
      organizationId="org"
      value={{ versionKey: "shared:1:" + "1".repeat(64) }}
      onSelectionChange={selectionChanged}
      onChange={changed}
    />,
  );
  await waitFor(() =>
    expect(changed).toHaveBeenLastCalledWith({
      expected_revision: 0,
      scheme: {
        scheme_id: "shared",
        version: 1,
        content_sha256: "1".repeat(64),
      },
    }),
  );
  expect(selectionChanged).not.toHaveBeenCalled();
});
it("restores task form and submits capture_plan in the creation command", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  saveDraft(draftKey("task-create", scope), {
    values: {
      name: "保留任务",
      datasetId: "__new_dataset__",
      type: "抓取",
      scenario: "工作台",
      description: "继续填写",
    },
    selection: {
      robotIds: ["r1", "r2"],
      versionKey: "shared:1:" + "1".repeat(64),
    },
  });
  mount(
    <CollectionTaskDrawer
      mode="create"
      projectId="project"
      scope={scope}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  );
  expect(screen.getByDisplayValue("保留任务")).toBeVisible();
  await screen.findByText(/2 维状态/);
  fireEvent.click(screen.getByRole("button", { name: "创建任务" }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalled());
  expect(onSubmit.mock.calls[0]![0]).toMatchObject({
    name: "保留任务",
    capture_plan: {
      scheme: {
        scheme_id: "shared",
        version: 1,
        content_sha256: "1".repeat(64),
      },
    },
  });
  expect(onSubmit.mock.calls[0]![0].capture_plan).not.toHaveProperty(
    "participants",
  );
  expect(
    vi
      .mocked(request)
      .mock.calls.some(
        ([o]) => o.path.endsWith("/capture-plan") && o.method === "PUT",
      ),
  ).toBe(false);
});
it("updates only device mapping and requires validation before READY", async () => {
  const user = userEvent.setup();
  mount(
    <RobotSchemeAssociations organizationId="org" robotId="r1" canManage />,
  );
  await user.click(await screen.findByRole("button", { name: "映射与默认项" }));
  await user.type(
    await screen.findByLabelText("LEROBOT_V3 observation.state 设备来源"),
    "actual.state",
  );
  await user.click(screen.getByRole("button", { name: "保存关联" }));
  expect(await screen.findByText("已关联 · 映射待验证")).toBeVisible();
  const command = vi
    .mocked(request)
    .mock.calls.find(([o]) => o.method === "PUT")![0].body;
  expect(command).toEqual({
    expected_revision: 1,
    scheme: { scheme_id: "shared", version: 1, content_sha256: "1".repeat(64) },
    mapping: {
      sources: { LEROBOT_V3: { "observation.state": "actual.state" } },
    },
    make_default: true,
  });
  expect(command).not.toHaveProperty("output");
});

it("checks published model compatibility and filters actual published software", async () => {
  const user = userEvent.setup();
  state.versions[0]!.model_version_ids = ["model-other"];
  state.draft.software = "unpublished software";
  mount(
    <RobotSchemeAssociations
      organizationId="org"
      robotId="r1"
      modelVersionId="model-current"
      canManage
    />,
  );
  await user.click(await screen.findByRole("button", { name: "关联方案" }));
  const software = state.versions[0]!.software;
  await user.type(screen.getByLabelText("采集软件筛选"), software);
  await user.click(screen.getByLabelText("关联采集方案"));
  await waitFor(() =>
    expect(
      screen.getByText(new RegExp(`${software}.*型号不兼容`)),
    ).toBeVisible(),
  );
  expect(screen.queryByText(/unpublished software/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存关联" })).toBeDisabled();
});

it("copies the selected immutable version instead of a newer draft or published version", async () => {
  const user = userEvent.setup();
  const newer = fixtureVersion(2);
  newer.name = "第二版";
  state.versions.unshift(newer);
  state.latest_version = 2;
  state.draft.name = "未发布草稿";
  mount(<Library />, "/settings/capture-schemes/shared?version=1");
  expect(await screen.findByText("已发布 v1 · 只读")).toBeVisible();
  expect(screen.queryByText("与上一发布版本比较")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "复制新方案" }));
  expect(await screen.findByLabelText("方案名称")).toHaveValue(
    "X1 关节方案 副本",
  );
  expect(state.versions).toHaveLength(2);
});

it("reuses an older definition as an optional shortcut while keeping the new scheme's basic information", async () => {
  const user = userEvent.setup();
  const originalVersion = state.versions[0]!;
  originalVersion.description = "包含双臂关节配置";
  originalVersion.model_version_ids = ["model-v1"];
  originalVersion.quality_policy = { missing_required: "reject" };
  originalVersion.adapters[0] = {
    source_format: "LEROBOT_V3",
    parser_version: "lerobot-v3/1",
    codebase_version: "v3.0",
    feature_sources: {
      "observation.state": "robot.joints",
      action: "robot.commands",
    },
  };
  const newer = fixtureVersion(2);
  newer.name = "第二版方案";
  newer.output.frequency_hz = 30;
  state.versions.unshift(newer);
  state.latest_version = 2;
  state.draft.name = "未发布修改";
  const original = structuredClone(state);
  const originalPlan = structuredClone(taskPlan);
  const fallback = vi.mocked(request).getMockImplementation()!;
  let created: SchemeDetail | undefined;
  vi.mocked(request).mockImplementation(async (options) => {
    if (options.path === "/organizations/org/capture-schemes/new-copy") {
      if (options.method === "PUT") {
        const { expected_revision, ...draft } = options.body as SchemeDraft;
        expect(expected_revision).toBe(0);
        created = {
          scheme_id: "new-copy",
          revision: 1,
          draft,
          latest_version: null,
          archived: false,
          versions: [],
          validations: [],
        };
      }
      return structuredClone(created);
    }
    return fallback(options);
  });
  const initial = {
    ...fixtureDocument(),
    name: "",
    description: "",
    software: "新采集端",
    model_version_ids: ["new-model"],
  };
  saveDraft(draftKey("scheme:new-copy", { organizationId: "org" }), {
    revision: 0,
    document: initial,
    step: 0,
  });
  mount(<Library />, "/settings/capture-schemes/new?draft=new-copy");
  await user.type(await screen.findByLabelText("方案名称"), "新采集方案");
  await user.type(screen.getByLabelText("方案说明"), "新工位配置");
  expect(
    screen.queryByText("从已有方案复制配置（可选）"),
  ).not.toBeInTheDocument();
  await user.click(screen.getByText("数据定义"));
  expect(screen.queryByLabelText("已有方案")).not.toBeInTheDocument();
  await user.click(screen.getByText("从已有方案复制配置（可选）"));
  await user.click(await screen.findByLabelText("已有方案"));
  await user.click(await screen.findByText("未发布修改"));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "填入配置" })).toBeEnabled(),
  );
  expect(screen.getByText("v2（最新发布）")).toBeVisible();
  await user.click(screen.getByLabelText("来源版本"));
  await user.click(await screen.findByText("v1"));
  await user.click(screen.getByRole("button", { name: "填入配置" }));
  await user.click(screen.getByText("适用范围"));
  expect(screen.getByLabelText("方案名称")).toHaveValue("新采集方案");
  expect(screen.getByLabelText("方案说明")).toHaveValue("新工位配置");
  await user.click(screen.getByText("数据定义"));
  expect(screen.getByLabelText("实际原始帧率")).toHaveValue("15");
  fireEvent.change(screen.getByLabelText("实际原始帧率"), {
    target: { value: "20" },
  });
  expect(
    screen.queryByRole("button", { name: "选择测试样本" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "从样本生成配置" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByText("原始文件配置"));
  expect(
    screen.getByText(
      "设置方案如何读取设备上传的原始文件：选择文件格式，并在字段名称不同时设置对应关系。",
    ),
  ).toBeVisible();
  expect(screen.getByLabelText("observation.state LeRobot 来源")).toHaveValue(
    "robot.joints",
  );
  await user.click(screen.getByText("确认与保存"));
  await user.click(screen.getByRole("button", { name: "保存方案" }));
  await waitFor(() => expect(created).toBeDefined());
  expect(created!.draft).toEqual({
    ...initial,
    name: "新采集方案",
    description: "新工位配置",
    adapters: originalVersion.adapters,
    quality_policy: originalVersion.quality_policy,
    output: { ...originalVersion.output, frequency_hz: 20 },
  });
  expect(created!.versions).toEqual([]);
  expect(created!.validations).toEqual([]);
  expect(state).toEqual(original);
  expect(taskPlan).toEqual(originalPlan);
  expect(
    vi
      .mocked(request)
      .mock.calls.every(([options]) =>
        options.path.startsWith("/organizations/org/capture-schemes"),
      ),
  ).toBe(true);
  await user.click(await screen.findByText("确认与保存"));
  expect(screen.getByRole("button", { name: "选择测试样本" })).toBeVisible();
  expect(screen.getByRole("button", { name: "发布新版本" })).toBeDisabled();
});

it.each([true, false])(
  "copies saved drafts, with published history: %s",
  async (published) => {
    const user = userEvent.setup();
    state.draft.name = "待发布的双臂方案";
    state.draft.description = "草稿中的新说明";
    state.draft.output.frequency_hz = 22;
    if (!published) {
      state.latest_version = null;
      state.versions = [];
      state.archived = true;
    }
    mount(<Library />, "/settings/capture-schemes/new?draft=draft-copy");
    await user.click(await screen.findByText("数据定义"));
    await user.click(screen.getByText("从已有方案复制配置（可选）"));
    await user.click(await screen.findByLabelText("已有方案"));
    await user.click(
      await screen.findByText(
        `${state.draft.name}${published ? "" : "（已归档）"}`,
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "填入配置" })).toBeEnabled(),
    );
    if (published) {
      await user.click(screen.getByRole("button", { name: "填入配置" }));
      expect(screen.getByLabelText("实际原始帧率")).toHaveValue("15");
      await user.click(screen.getByLabelText("来源版本"));
      await user.click(await screen.findByText("当前已保存草稿"));
    }
    await user.click(screen.getByRole("button", { name: "填入配置" }));
    expect(screen.getByLabelText("实际原始帧率")).toHaveValue("22");
    await user.click(screen.getByText("适用范围"));
    expect(screen.getByLabelText("方案名称")).toHaveValue("");
    expect(screen.getByLabelText("方案说明")).toHaveValue("");
    expect(
      vi
        .mocked(request)
        .mock.calls.every(([options]) => options.method === "GET"),
    ).toBe(true);
  },
);

it("preserves current edits when another source fails and loads it only after a successful retry", async () => {
  const user = userEvent.setup();
  const other = { ...fixtureDetail(), scheme_id: "other" };
  other.draft.name = "另一套方案";
  other.versions[0]!.name = "另一套方案";
  other.versions[0]!.scheme_id = "other";
  other.versions[0]!.output.frequency_hz = 30;
  const fallback = vi.mocked(request).getMockImplementation()!;
  let failed = true;
  vi.mocked(request).mockImplementation(async (options) => {
    if (options.path.endsWith("/capture-schemes"))
      return {
        items: [state, other].map((item) => ({
          ...item,
          instance_count: 0,
          task_count: 0,
        })),
      };
    if (options.path.endsWith("/capture-schemes/other")) {
      if (failed) throw new Error("暂时无法读取");
      return structuredClone(other);
    }
    return fallback(options);
  });
  mount(<Library />, "/settings/capture-schemes/new?draft=retry-copy");
  await user.click(await screen.findByText("数据定义"));
  await user.click(screen.getByText("从已有方案复制配置（可选）"));
  await user.click(await screen.findByLabelText("已有方案"));
  await user.click(await screen.findByText("X1 关节方案"));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "填入配置" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "填入配置" }));
  fireEvent.change(screen.getByLabelText("实际原始帧率"), {
    target: { value: "20" },
  });
  await user.click(screen.getByLabelText("已有方案"));
  await user.click(await screen.findByText("另一套方案"));
  expect(await screen.findByText(/方案配置读取失败/)).toBeVisible();
  expect(screen.getByLabelText("实际原始帧率")).toHaveValue("20");
  expect(screen.getByRole("button", { name: "填入配置" })).toBeDisabled();
  failed = false;
  await user.click(screen.getByRole("button", { name: "重新读取" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "填入配置" })).toBeEnabled(),
  );
  expect(screen.getByLabelText("实际原始帧率")).toHaveValue("20");
  await user.click(screen.getByRole("button", { name: "填入配置" }));
  expect(screen.getByLabelText("实际原始帧率")).toHaveValue("30");
});

it("searches the list and archives only after confirmation, retaining published references", async () => {
  const user = userEvent.setup();
  mount(<Library />, "/settings/capture-schemes");
  await screen.findByText("X1 关节方案");
  await user.type(screen.getByLabelText("搜索采集方案"), "X1");
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "GET",
        query: { q: "X1", source_format: undefined, include_archived: false },
      }),
    ),
  );
  await user.click(await screen.findByRole("button", { name: /归\s*档/ }));
  expect(state.archived).toBe(false);
  await user.click(screen.getAllByRole("button", { name: /归\s*档/ }).at(-1)!);
  await waitFor(() => expect(state.archived).toBe(true));
  expect(state.versions).toHaveLength(1);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/organizations/org/capture-schemes/shared:archive",
      body: { expected_revision: 1 },
    }),
  );
});

it("upgrades explicitly and removes obsolete device sources from the new mapping only", async () => {
  const user = userEvent.setup();
  state.versions.unshift(fixtureVersion(2));
  state.latest_version = 2;
  const old = links.r1![0]!;
  old.mapping.mapping.sources = {
    LEROBOT_V3: {
      "observation.state": "robot.state",
      removed_camera: "old.camera",
    },
  };
  const original = structuredClone(old);
  mount(
    <RobotSchemeAssociations organizationId="org" robotId="r1" canManage />,
  );
  await user.click(await screen.findByRole("button", { name: "升级版本" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保存关联" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "保存关联" }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "PUT",
        body: {
          expected_revision: 1,
          scheme: {
            scheme_id: "shared",
            version: 2,
            content_sha256: fixtureVersion(2).content_sha256,
          },
          mapping: {
            sources: { LEROBOT_V3: { "observation.state": "robot.state" } },
          },
          make_default: true,
        },
      }),
    ),
  );
  expect(old).toEqual(original);
});

it("provides a visible parent route on create and calls editing a scheme", async () => {
  const user = userEvent.setup();
  mount(<Library />, "/settings/capture-schemes");
  await screen.findByText("X1 关节方案");
  await user.click(screen.getByRole("button", { name: "新建方案" }));
  expect(
    await screen.findByRole("heading", { name: "新建方案" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "新建方案" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "返回方案列表" }));
  await user.click(await screen.findByRole("button", { name: "编辑方案" }));
  expect(
    await screen.findByRole("heading", { name: "编辑方案" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "返回方案列表" })).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "保存方案" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByText("确认与保存"));
  expect(screen.getByRole("button", { name: "保存方案" })).toBeVisible();
  expect(screen.queryByText(/草稿/)).not.toBeInTheDocument();
});
