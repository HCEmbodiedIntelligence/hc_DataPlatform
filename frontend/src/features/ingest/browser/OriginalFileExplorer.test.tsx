// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import OriginalFileExplorer from "./OriginalFileExplorer";
import {
  authorizeOriginalFile,
  getOriginalDirectory,
  getOriginalTextPreview,
} from "./original-source-api";

vi.mock("./original-source-api", () => ({
  authorizeOriginalFile: vi.fn(),
  getOriginalDirectory: vi.fn(),
  getOriginalTextPreview: vi.fn(),
}));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "local",
};
const file = {
  name: "info.json",
  path: "meta/info.json",
  kind: "FILE" as const,
  size: 42,
  file_count: 1,
  sha256: "a".repeat(64),
  storage_key: "raw/source/meta/info.json",
  local_path: "/local/files/raw/source/meta/info.json",
  text_preview: true,
};
const folder = {
  name: "meta",
  path: "meta",
  kind: "DIRECTORY" as const,
  size: 42,
  file_count: 1,
  text_preview: false,
};
beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  );
  vi.mocked(getOriginalDirectory).mockImplementation(
    async (_scope, importId, path, offset) => ({
      import_id: importId,
      path,
      offset,
      local_path: `/local/files/raw/source${path ? `/${path}` : ""}`,
      storage_prefix: "raw/source",
      entries: path === "meta" ? [file] : [folder],
      total: 1,
      file_count: 1,
      total_bytes: 42,
    }),
  );
  vi.mocked(getOriginalTextPreview).mockResolvedValue({
    path: file.path,
    text: '<script>alert("hello")</script>',
    truncated: true,
    preview_bytes: 42,
  });
  vi.mocked(authorizeOriginalFile).mockResolvedValue({
    path: file.path,
    url: "https://files.invalid/download",
    expires_at: "2099-01-01T00:00:00Z",
  });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function mount(canDownload = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OriginalFileExplorer
        scope={scope}
        importId="raw"
        canDownload={canDownload}
      />
    </QueryClientProvider>,
  );
}
it("opens folders, previews escaped text and returns to the root", async () => {
  const view = mount();
  fireEvent.click(await screen.findByRole("button", { name: "meta" }));
  expect(await screen.findByText("/local/files/raw/source/meta")).toBeVisible();
  fireEvent.click(await screen.findByRole("button", { name: "info.json" }));
  expect(
    await screen.findByText('<script>alert("hello")</script>'),
  ).toBeVisible();
  expect(view.container.querySelector("script")).toBeNull();
  expect(
    screen.getByText("仅预览前 256 KB，完整内容请下载原文件"),
  ).toBeVisible();
  expect(screen.getByText(file.local_path)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "返回上级文件夹" }));
  await screen.findByRole("button", { name: "meta" });
  expect(
    screen.queryByRole("region", { name: "文件详情" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "返回上级文件夹" })).toBeDisabled();
});
it("downloads the selected original file through an exact scoped grant", async () => {
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "meta" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "下载 info.json" }),
  );
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(authorizeOriginalFile).toHaveBeenCalledWith(
    scope,
    "raw",
    file.path,
    true,
    expect.any(AbortSignal),
  );
});
it("keeps downloads disabled when the entry point denies them", async () => {
  mount(false);
  fireEvent.click(await screen.findByRole("button", { name: "meta" }));
  expect(
    await screen.findByRole("button", { name: "下载 info.json" }),
  ).toBeDisabled();
  expect(authorizeOriginalFile).not.toHaveBeenCalled();
});
it("shows the directory error with retry rather than cached file rows", async () => {
  vi.mocked(getOriginalDirectory).mockRejectedValue(new Error("文件夹不存在"));
  mount();
  expect(await screen.findByText("文件夹不存在")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "meta" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /重\s*试/u })).toBeVisible();
});
