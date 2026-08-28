// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import DataSourcesPage from "./page";

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
      allowedActions: [],
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
  useDataSourceMutation: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
    reset: vi.fn(),
  }),
  useTestDataSourceConnection: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
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
    has: (capability: string) => capability === "ingest_source.read",
    loading: false,
    failed: false,
  }),
}));

vi.mock("../../shared/ui", () => ({
  DataCursorPager: () => null,
  EntityDrawer: () => null,
  FilterToolbar: () => null,
  PageState: () => null,
  StandardPageScaffold: ({
    header,
  }: {
    readonly header: {
      readonly actions?: ReactNode;
      readonly breadcrumbs?: readonly {
        readonly key: string;
        readonly label: string;
        readonly to?: string;
      }[];
    };
  }) => (
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
  ),
  StatusTag: () => null,
}));

vi.mock("./components/DataSourceTable", () => ({
  DataSourceTable: () => null,
}));
vi.mock("./components/SourceActionDialogs", () => ({
  ConfirmSourceStateDialog: () => null,
  ConnectorDeleteConfirmDialog: () => null,
  CredentialRotationDialog: () => null,
}));
vi.mock("./components/SourceEditorDialog", () => ({
  SourceEditorDialog: () => null,
}));

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
});
