// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DataSourcesPage from "./page";

const {
  access,
  bindModelMutateAsync,
  createRobotMutateAsync,
  createSourceMutateAsync,
} = vi.hoisted(() => ({
  access: { canManage: false },
  bindModelMutateAsync: vi.fn(),
  createRobotMutateAsync: vi.fn(),
  createSourceMutateAsync: vi.fn(),
}));

vi.mock("../../features/ingest/api", () => ({
  useDataSourcesPage: () => ({
    isPending: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    data: {
      items: [],
      pageInfo: {
        startCursor: null,
        endCursor: null,
        hasPreviousPage: false,
        hasNextPage: false,
      },
      snapshotAt: "2026-08-24T00:00:00Z",
      allowedActions: access.canManage ? ["CREATE"] : [],
      componentErrors: [],
      summary: {
        totalCount: "0",
        onlineCount: "0",
        verifiedBytesToday: "0",
        abnormalCount: "0",
      },
    },
  }),
  useDataSource: () => ({
    data: undefined,
    dataUpdatedAt: 0,
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useDataSourceMutation: (operation: string) => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
    mutateAsync: operation === "create" ? createSourceMutateAsync : vi.fn(),
    reset: vi.fn(),
  }),
  useTestDataSourceConnection: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock("../../features/robots/api", () => ({
  getRobotBootstrap: vi.fn(),
  useCreateRobot: () => ({
    isPending: false,
    error: null,
    mutateAsync: createRobotMutateAsync,
    reset: vi.fn(),
  }),
  useRobotBootstrap: () => ({
    data: undefined,
    isPending: false,
    isError: false,
  }),
}));

vi.mock("../../features/robot-models/api", () => ({
  useBindRobotModelVersion: () => ({
    isPending: false,
    error: null,
    mutateAsync: bindModelMutateAsync,
    reset: vi.fn(),
  }),
}));

vi.mock("../../features/ingest/use-ingest-scope", () => ({
  useIngestScope: () => ({
    organizationId: "org-1",
    projectId: "project-1",
    regionCode: "cn-east-1",
  }),
}));

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) =>
      capability === "ingest_source.read" ||
      (access.canManage &&
        ["ingest_source.manage", "robot.manage", "robot_model.manage"].includes(
          capability,
        )),
    loading: false,
    failed: false,
  }),
}));

vi.mock("../../shared/ui", () => ({
  CursorPager: () => null,
  EntityDrawer: () => null,
  FilterToolbar: () => null,
  PageState: ({ action }: { readonly action?: ReactNode }) => (
    <div>{action}</div>
  ),
  StandardPageScaffold: ({
    header,
    state,
  }: {
    readonly header: {
      readonly actions?: ReactNode;
      readonly breadcrumbs?: readonly {
        readonly key: string;
        readonly label: string;
        readonly to?: string;
      }[];
    };
    readonly state?: ReactNode;
  }) => (
    <>
      <header>
        <nav>
          {header.breadcrumbs?.map((item) =>
            item.to ? (
              <a href={item.to} key={item.key}>
                {item.label}
              </a>
            ) : (
              <span key={item.key}>{item.label}</span>
            ),
          )}
        </nav>
        {header.actions}
      </header>
      {state}
    </>
  ),
  StatusTag: () => null,
}));

vi.mock("./components/DataSourceTable", () => ({
  DataSourceTable: () => null,
}));
vi.mock("./components/RobotIdentityConsole", () => ({
  RobotIdentityConsole: () => null,
}));
vi.mock("./components/SourceActionDialogs", () => ({
  ConfirmSourceStateDialog: () => null,
  ConnectorDeleteConfirmDialog: () => null,
  CredentialRotationDialog: () => null,
}));
vi.mock("./components/SourceEditorDialog", () => ({
  SourceEditorDialog: (props: {
    readonly open: boolean;
    readonly mode: string;
    readonly initialKind?: string;
    readonly onSubmit: (draft: unknown) => void;
  }) =>
    props.open && props.mode === "create" ? (
      <button
        data-initial-kind={props.initialKind}
        onClick={() =>
          props.onSubmit({
            name: "装配工位数据源",
            sourceFormat: "MULTI_FORMAT",
            sourceFormatVersion: null,
            uploadPolicyCode: "STANDARD",
            configuration: { kind: "ROBOT", transport: "HTTPS" },
            binding: { kind: "ROBOT", robotId: "" },
            robotProvisioning: {
              mode: "CREATE",
              displayName: "装配机器人 A",
              serialNo: "SN-001",
              modelVersionId: "model-version-alpha",
            },
          })
        }
      >
        提交机器人数据源
      </button>
    ) : null,
}));

beforeEach(() => {
  access.canManage = false;
  createRobotMutateAsync.mockReset();
  bindModelMutateAsync.mockReset();
  createSourceMutateAsync.mockReset();
});

afterEach(cleanup);

describe("data sources navigation", () => {
  it("links upload tasks to the new-upload mode and the parent breadcrumb to records", () => {
    render(
      <MemoryRouter initialEntries={["/ingest/sources"]}>
        <DataSourcesPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "上传任务" })).toHaveAttribute(
      "href",
      "/ingest/uploads/new",
    );
    expect(screen.getByRole("link", { name: "数据接入" })).toHaveAttribute(
      "href",
      "/ingest/uploads/records",
    );
  });

  it("creates the instance, binds the published model, then creates the data source", async () => {
    access.canManage = true;
    createRobotMutateAsync.mockResolvedValue({
      id: "robot-created",
      etag: "robot-etag",
      effectiveModelBinding: null,
    });
    bindModelMutateAsync.mockResolvedValue({ id: "binding-created" });
    createSourceMutateAsync.mockResolvedValue({ id: "source-created" });
    const user = userEvent.setup();

    render(
      <MemoryRouter
        initialEntries={["/ingest/sources?sourceType=ROBOT&intent=create"]}
      >
        <DataSourcesPage />
      </MemoryRouter>,
    );
    const submit = screen.getByRole("button", { name: "提交机器人数据源" });
    expect(submit).toHaveAttribute("data-initial-kind", "ROBOT");
    await user.click(submit);

    await waitFor(() => expect(createSourceMutateAsync).toHaveBeenCalled());
    expect(createRobotMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        displayName: "装配机器人 A",
        serialNo: "SN-001",
        lifecycleStatus: "ACTIVE",
      }),
    );
    expect(bindModelMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        versionId: "model-version-alpha",
        robotId: "robot-created",
        robotEtag: "robot-etag",
      }),
    );
    expect(createSourceMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          source_type: "ROBOT",
          source_format: "MULTI_FORMAT",
          source_format_version: null,
          binding: { kind: "ROBOT", robot_id: "robot-created" },
        }),
      }),
    );
    expect(createRobotMutateAsync.mock.invocationCallOrder[0]).toBeLessThan(
      bindModelMutateAsync.mock.invocationCallOrder[0]!,
    );
    expect(bindModelMutateAsync.mock.invocationCallOrder[0]).toBeLessThan(
      createSourceMutateAsync.mock.invocationCallOrder[0]!,
    );
  });

  it("offers a robot-specific creation action when the project has no sources", async () => {
    access.canManage = true;
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/ingest/sources"]}>
        <DataSourcesPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "新建机器人数据源" }));
    expect(
      screen.getByRole("button", { name: "提交机器人数据源" }),
    ).toHaveAttribute("data-initial-kind", "ROBOT");
  });
});
