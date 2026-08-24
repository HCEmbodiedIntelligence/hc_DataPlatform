// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import DataSchemasPage from "./page";

const {
  createMutate,
  associateDatasetReferenceMutate,
  listDataSchemas,
  preflightMutate,
  publishMutate,
  updateMutate,
  validateMutate,
} = vi.hoisted(() => ({
  createMutate: vi.fn(),
  associateDatasetReferenceMutate: vi.fn(),
  listDataSchemas: vi.fn(),
  preflightMutate: vi.fn(),
  publishMutate: vi.fn(),
  updateMutate: vi.fn(),
  validateMutate: vi.fn(),
}));

const schema = {
  schemaId: "camera-schema",
  familyId: "camera-family",
  version: "1",
  displayName: "前视图像",
  logicalType: "IMAGE",
  status: "DRAFT",
  compatibilityMode: "BACKWARD",
  compatibilityResult: null,
  hash: {
    algorithm: "SHA-256",
    canonicalizationVersion: "schema-c14n-v1",
    value: "a".repeat(64),
  },
  definition: {
    fields: [
      { name: "image", type: "bytes", description: "payload", required: true },
    ],
  },
  etag: '"camera-schema:1:1"',
  allowedActions: ["VIEW", "EDIT", "VALIDATE", "PUBLISH"],
  blockedReasons: [],
} as const;

type PageSchema = Omit<typeof schema, "status"> & {
  readonly status: "DRAFT" | "PUBLISHED";
};
let renderedSchema: PageSchema = schema;

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => true, loading: false, failed: false }),
}));

vi.mock("../../features/data-schemas/api", () => ({
  useDataSchemas: (filters: unknown) => {
    listDataSchemas(filters);
    return {
      isPending: false,
      error: null,
      refetch: vi.fn(),
      data: {
        items: [schema],
        pageInfo: {
          start_cursor: null,
          end_cursor: null,
          has_previous_page: false,
          has_next_page: false,
        },
        snapshotAt: "2026-08-21T08:00:00Z",
      },
    };
  },
  useDataSchemaVersion: () => ({ data: renderedSchema }),
  useDataSchemaDatasetReferences: () => ({
    isPending: false,
    error: null,
    data: [],
    refetch: vi.fn(),
  }),
  useResolveDataSchemaRoute: () => ({
    isPending: false,
    error: null,
    data: null,
  }),
  useCreateStreamSchema: () => ({ isPending: false, mutate: createMutate }),
  useUpdateStreamSchemaDraft: () => ({
    isPending: false,
    mutate: updateMutate,
  }),
  useValidateStreamSchemaVersion: () => ({
    isPending: false,
    mutate: validateMutate,
  }),
  usePreflightDataSchemaPublish: () => ({
    isPending: false,
    mutate: preflightMutate,
  }),
  usePublishDataSchema: () => ({ isPending: false, mutate: publishMutate }),
  useAssociateDataSchemaDatasetReference: () => ({
    isPending: false,
    mutate: associateDatasetReferenceMutate,
  }),
}));

vi.mock("../../features/data-schemas/registry-rules", () => ({
  schemaTelemetryProjection: () => ({ schema_id: "camera-schema" }),
}));

function renderPage(
  path = "/settings/data-schemas?schemaId=camera-schema&schemaVersion=1",
) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <DataSchemasPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  renderedSchema = schema;
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  if (!globalThis.crypto.randomUUID) {
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: () => "p17-test-key",
    });
  }
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
  validateMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.({
      id: "report-1",
      schema_id: schema.schemaId,
      schema_version: schema.version,
      content_hash: schema.hash.value,
      compatibility_check_id: "compatibility-1",
      compatibility_result: "PASSED",
      status: "PASSED",
      findings: [],
      checked_by: "schema-publisher",
      checked_at: "2026-08-21T08:00:00Z",
    }),
  );
  preflightMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.({ allowed: true, preflight_token: "p".repeat(64) }),
  );
  publishMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(schema),
  );
  createMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(schema),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("P17 real schema authoring page", () => {
  it("replaces disabled authoring placeholders with an ETag validation and publish flow", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByRole("button", { name: "新建 Schema" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "导入定义" })).toBeEnabled();
    expect(screen.queryByText("当前范围未开放")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "校验版本" }));
    await waitFor(() => expect(validateMutate).toHaveBeenCalledTimes(1));
    expect(screen.getByText("校验通过，可进行发布预检。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "发布预检" }));
    await waitFor(() => expect(preflightMutate).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "发布版本" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "发布版本" }));
    await waitFor(() => expect(publishMutate).toHaveBeenCalledTimes(1));
    expect(publishMutate.mock.calls[0]?.[0]).toMatchObject({
      schemaId: "camera-schema",
      schemaVersion: "1",
      etag: '"camera-schema:1:1"',
      preflightToken: "p".repeat(64),
    });
  });

  it("opens a real import editor rather than a disabled action", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "导入定义" }));
    expect(screen.getByText("导入 Schema 定义")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("camera-front")).toBeEnabled();
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeEnabled();
  });

  it("sends URL-bound status, logical type, cursor, and page size to the list query", () => {
    renderPage(
      "/settings/data-schemas?q=front&status=PUBLISHED&logicalType=DEPTH&after=bound-cursor&limit=50",
    );

    expect(listDataSchemas).toHaveBeenLastCalledWith({
      q: "front",
      status: "PUBLISHED",
      logicalType: "DEPTH",
      after: "bound-cursor",
      limit: 50,
    });
  });

  it("applies a selected category as a logical-type filter and resets its page cursor", async () => {
    const user = userEvent.setup();
    renderPage("/settings/data-schemas?q=front&after=bound-cursor&limit=50");

    await user.click(screen.getByRole("button", { name: "动作" }));
    await user.click(screen.getByRole("button", { name: "应用筛选" }));

    await waitFor(() =>
      expect(listDataSchemas).toHaveBeenLastCalledWith({
        q: "front",
        logicalType: "ACTION",
        limit: 50,
      }),
    );
  });

  it("records a READY dataset version against an immutable published schema", async () => {
    const user = userEvent.setup();
    renderedSchema = { ...schema, status: "PUBLISHED" };
    renderPage(
      "/settings/data-schemas?schemaId=camera-schema&schemaVersion=1&detailTab=references",
    );

    expect(
      screen.getByRole("button", { name: "关联数据集版本" }),
    ).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "关联数据集版本" }));
    await user.type(
      screen.getByPlaceholderText("dataset_camera_front"),
      "dataset_camerafront",
    );
    await user.type(
      screen.getByPlaceholderText("version_release_001"),
      "version_release001",
    );
    await user.click(screen.getByRole("button", { name: "固定引用" }));

    expect(associateDatasetReferenceMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        schemaId: "camera-schema",
        schemaVersion: "1",
        etag: '"camera-schema:1:1"',
        input: {
          dataset_id: "dataset_camerafront",
          dataset_version_id: "version_release001",
        },
      }),
      expect.any(Object),
    );
  });
});
