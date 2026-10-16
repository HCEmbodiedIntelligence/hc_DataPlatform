// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../api/http-client";
import { useShellStore } from "../scope/shell-store";
import {
  ResourceLabel,
  decodedFileName,
  readableFallback,
} from "./resource-labels";

vi.mock("../api/http-client", () => ({ request: vi.fn() }));
const a = { organizationId: "org-a", projectId: "p", regionCode: "global" };
const b = { organizationId: "org-b", projectId: "p", regionCode: "global" };
let client: QueryClient;
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "labels", displayName: "Labels", roleIds: [] },
    "label-token",
  );
  store.setSessionScopes(
    [a, b].map((scope) => ({
      ...scope,
      organizationName: scope.organizationId,
      projectName: "Project",
      regionCodes: [scope.regionCode],
      projectWide: false,
      capabilities: ["dataset.read", "upload.read"],
    })),
    1,
  );
  store.setScope(a);
  vi.mocked(request).mockImplementation(async (options) => {
    const refs = (
      options.body as {
        references: { kind: string; id: string; dataset_id?: string }[];
      }
    ).references;
    return {
      items: refs.map((ref) => ({
        ...ref,
        dataset_id: ref.dataset_id ?? null,
        label:
          ref.kind === "collection_task"
            ? "12"
            : `数据集 ${options.scope?.organizationId}`,
        description_label: null,
      })),
    } as never;
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  useShellStore.getState().setSession(null, null);
  vi.resetAllMocks();
});

it("batches and deduplicates visible references while keeping equal IDs in separate organizations", async () => {
  render(
    <QueryClientProvider client={client}>
      <ResourceLabel
        kind="dataset"
        id="dataset_1234567890abcdef1234567890abcdef"
        scope={a}
      />
      <ResourceLabel
        kind="dataset"
        id="dataset_1234567890abcdef1234567890abcdef"
        scope={a}
      />
      <ResourceLabel
        kind="collection_task"
        id="12345678-abcd-1234-abcd-123456789012"
        scope={a}
      />
      <ResourceLabel
        kind="dataset"
        id="dataset_1234567890abcdef1234567890abcdef"
        scope={b}
      />
    </QueryClientProvider>,
  );
  expect(await screen.findAllByText("数据集 org-a")).toHaveLength(2);
  expect(await screen.findByText("数据集 org-b")).toBeVisible();
  expect(screen.getByText("12")).toBeVisible();
  expect(request).toHaveBeenCalledTimes(2);
  const calls = vi.mocked(request).mock.calls;
  expect(
    calls
      .map(
        ([options]) =>
          (options.body as { references: unknown[] }).references.length,
      )
      .sort(),
  ).toEqual([1, 2]);
  expect(
    screen.queryByText("12345678-abcd-1234-abcd-123456789012"),
  ).not.toBeInTheDocument();
  act(() => useShellStore.getState().setSession(null, null));
  await waitFor(() =>
    expect(screen.queryByText("数据集 org-b")).not.toBeInTheDocument(),
  );
});

it("decodes actual filenames once and keeps opaque IDs out of visible fallbacks", () => {
  expect(
    decodedFileName("uploads/%E6%8A%93%E5%8F%96%20%E6%B5%8B%E8%AF%95.mcap"),
  ).toBe("抓取 测试.mcap");
  expect(decodedFileName("100%25_ready.mcap")).toBe("100%_ready.mcap");
  expect(decodedFileName("invalid%name.mcap")).toBe("invalid%name.mcap");
  expect(readableFallback("episode", "episode_0001")).toBe("Episode 1");
  expect(readableFallback("collection_task", "00000012")).toBe("12");
  expect(
    readableFallback(
      "dataset",
      "dataset_task_be7c5c088622b170f0669f7d97a2b835",
    ),
  ).toBe("数据集");
});
