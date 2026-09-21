// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useShellStore } from "../../shared/scope/shell-store";
import { ManualIssuesPage } from "./page";

const { requestMock, previewMock, hasCapability } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  previewMock: vi.fn(),
  hasCapability: vi.fn(() => true),
}));

vi.mock("../../shared/api/http-client", () => ({ request: requestMock }));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: hasCapability,
    loading: false,
    failed: false,
  }),
}));
vi.mock("../../features/cleaning/api", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../features/cleaning/api")>();
  const emptyQuery = () => ({
    isPending: false,
    isFetching: false,
    error: null,
  });
  const mutation = () => ({ isPending: false, mutate: vi.fn() });
  return {
    ...actual,
    useManualIssues: emptyQuery,
    useManualIssuesPage: emptyQuery,
    useManualIssue: emptyQuery,
    useTriageManualIssue: mutation,
    useResolveManualIssue: mutation,
    useCreateDraftFromManualIssue: mutation,
  };
});
vi.mock("../p03-upload-jobs/components/OriginalSourceBrowser", () => ({
  default: (props: { importId: string; initialEpisodeIndex: number }) => {
    previewMock(props);
    return (
      <p>
        原始预览 {props.importId} / Episode {props.initialEpisodeIndex}
      </p>
    );
  },
}));

const scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "cn-test",
};
const wireProblem = {
  schema_version: "auto-quality-problem/v1",
  id: `qc_${"a".repeat(32)}`,
  source: "AUTO_QC",
  session_id: null,
  source_import_id: "native-import-a",
  source_episode_index: 7,
  rollout_id: "lerobot-native-ep-000007",
  data_package_id: "lerobot-native-ep-000007",
  status: "REJECT",
  severity: "CRITICAL",
  start_ns: "0",
  end_ns: "1000000000",
  message: "black or underexposed frame ratio exceeds the profile limit",
  finding_count: 1,
  finding_codes: ["QC_IMAGE_BLACK"],
  topics: ["camera/front"],
  report_sha256: "a".repeat(64),
  updated_at: "2026-09-21T01:00:00Z",
};
const returnTo = "/manual/issues?source=AUTO_QC&severity=CRITICAL";
let client: QueryClient;
let progress = processingFixture();
let qualityPassed = false;
let problemsCleared = false;

function processingFixture() {
  return {
    import_id: "native-import-a",
    dataset_id: "dataset-a",
    status: "PARTIALLY_FAILED",
    episode_count: 10,
    ready: 8,
    failed: 2,
    last_error_code: null,
    updated_at: "2026-09-21T01:00:00Z",
    source_format: "LEROBOT_V3",
    file_count: 50,
    total_bytes: 10000,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  hasCapability.mockImplementation(() => true);
  progress = processingFixture();
  qualityPassed = false;
  problemsCleared = false;
  useShellStore.setState({
    scope,
    bootstrapLoaded: true,
    authorizationFailed: false,
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup();
  client.clear();
  useShellStore.setState({ scope: null });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function CurrentLocation() {
  const location = useLocation();
  return (
    <output aria-label="当前地址">
      {location.pathname}
      {location.search}
    </output>
  );
}
function renderProblem(overrides: Record<string, unknown> = {}) {
  requestMock.mockImplementation(
    async ({ path, method }: { path: string; method: string }) => {
      if (path.endsWith("/quality-problems"))
        return {
          schema_version: "auto-quality-problem-list/v1",
          items: problemsCleared ? [] : [{ ...wireProblem, ...overrides }],
          total: problemsCleared ? 0 : 1,
        };
      if (path.endsWith("/processing")) return { ...progress };
      if (path.endsWith("/quality"))
        return {
          status: qualityPassed ? "PASS" : wireProblem.status,
          findings: qualityPassed
            ? []
            : [{ code: "QC_IMAGE_BLACK", topic: "camera/front" }],
        };
      if (method === "POST" && path.endsWith(":retry")) {
        progress = {
          ...progress,
          status: "PENDING",
          failed: 0,
          updated_at: "2026-09-21T01:01:00Z",
        };
        return { ...progress };
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    },
  );
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[returnTo]}>
        <ManualIssuesPage />
        <CurrentLocation />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("问题数据 Raw 入口", () => {
  it.each([0, 7])(
    "opens the native source at Episode %i without an upload session",
    async (episodeIndex) => {
      renderProblem({ source_episode_index: episodeIndex });
      fireEvent.click(
        await screen.findByRole("button", { name: "查看 Raw 诊断" }),
      );
      const dialog = await screen.findByRole("dialog");
      await waitFor(() =>
        expect(
          within(dialog).getByText(
            `原始预览 native-import-a / Episode ${episodeIndex}`,
          ),
        ).toBeVisible(),
      );
      expect(previewMock).toHaveBeenLastCalledWith(
        expect.objectContaining({
          scope,
          importId: "native-import-a",
          initialEpisodeIndex: episodeIndex,
        }),
      );
      expect(within(dialog).getByText("黑帧比例超标")).toBeVisible();
      expect(
        within(dialog).getByText(
          "视频中黑屏或曝光不足的画面占比超过允许范围。",
        ),
      ).toBeVisible();
      expect(dialog).not.toHaveTextContent("问题代码");
      expect(dialog).not.toHaveTextContent("QC_IMAGE_BLACK");
      expect(dialog).not.toHaveTextContent(wireProblem.message);
      expect(screen.getByLabelText("当前地址")).toHaveTextContent(returnTo);
      fireEvent.click(within(dialog).getByRole("button", { name: /close/i }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(screen.getByLabelText("当前地址")).toHaveTextContent(returnTo);
    },
  );

  it("opens the same native preview from the problem summary", async () => {
    renderProblem();
    fireEvent.click(
      await screen.findByRole("button", {
        name: /黑帧比例超标 视频中黑屏或曝光不足/,
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByText("原始预览 native-import-a / Episode 7"),
      ).toBeVisible(),
    );
  });

  it("preserves the upload-session diagnostic link for older responses", async () => {
    renderProblem({
      session_id: "session-a",
      source_import_id: undefined,
      source_episode_index: undefined,
    });
    const link = await screen.findByRole("link", { name: "查看 Raw 诊断" });
    const target = new URL(link.getAttribute("href")!, "https://local.invalid");
    expect(target.pathname).toBe("/manual/issues/raw-diagnostic/session-a");
    const back = new URL(
      target.searchParams.get("returnTo")!,
      "https://local.invalid",
    );
    expect(back.pathname).toBe("/manual/issues");
    expect(back.searchParams.get("source")).toBe("AUTO_QC");
    expect(back.searchParams.get("severity")).toBe("CRITICAL");
  });

  it("keeps an unavailable source disabled instead of opening the wrong episode", async () => {
    renderProblem({ source_episode_index: null });
    expect(
      await screen.findByRole("button", { name: "Raw 入口不可用" }),
    ).toBeDisabled();
    expect(previewMock).not.toHaveBeenCalled();
  });

  it("retries the selected upload, keeps the resolved diagnostic open, and shows completion", async () => {
    renderProblem();
    fireEvent.click(
      await screen.findByRole("button", { name: "查看 Raw 诊断" }),
    );
    const dialog = await screen.findByRole("dialog");
    const retryButton = await within(dialog).findByRole("button", {
      name: "重新质检并继续处理",
    });
    await waitFor(() => expect(retryButton).toBeEnabled());
    qualityPassed = true;
    problemsCleared = true;
    fireEvent.click(retryButton);
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(
        expect.objectContaining({
          method: "POST",
          path: "/projects/project-a/regions/cn-test/lerobot-imports/native-import-a:retry",
          scope,
        }),
      ),
    );
    expect(await within(dialog).findByText("当前条目质检通过")).toBeVisible();
    expect(
      within(dialog).getByRole("button", { name: "正在处理，请等待" }),
    ).toBeDisabled();
    expect(within(dialog).getByLabelText("上传批次处理进度")).toHaveTextContent(
      "等待或处理中 2 条",
    );
    expect(within(dialog).queryByText("黑帧比例超标")).not.toBeInTheDocument();
    progress = {
      ...progress,
      status: "SUCCEEDED",
      ready: 10,
      updated_at: "2026-09-21T01:02:00Z",
    };
    fireEvent.click(within(dialog).getByRole("button", { name: "刷新状态" }));
    expect(
      await within(dialog).findByText("处理完成，可前往数据集查看和标注"),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("link", { name: "进入数据集查看已入库数据" }),
    ).toHaveAttribute("href", "/datasets/dataset-a");
    expect(
      within(dialog).getByRole("link", { name: "上传修正数据" }),
    ).toHaveAttribute("href", "/ingest/uploads/new");
    expect(
      within(dialog).getByRole("button", { name: "重新质检并继续处理" }),
    ).toBeDisabled();
    expect(
      requestMock.mock.calls.filter(([arg]) => arg.method === "POST"),
    ).toHaveLength(1);
  });

  it("does not offer mutations or dataset access without the corresponding permissions", async () => {
    hasCapability.mockImplementation(
      (capability?: string) =>
        !["upload.manage", "dataset.read"].includes(capability ?? ""),
    );
    renderProblem();
    fireEvent.click(
      await screen.findByRole("button", { name: "查看 Raw 诊断" }),
    );
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(
        within(dialog).getByText(
          "请有上传管理权限的成员重新处理或上传修正数据。",
        ),
      ).toBeVisible(),
    );
    expect(
      within(dialog).queryByRole("button", { name: "重新质检并继续处理" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "上传修正数据" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "进入数据集查看已入库数据" }),
    ).not.toBeInTheDocument();
    expect(
      requestMock.mock.calls.filter(([arg]) => arg.method === "POST"),
    ).toHaveLength(0);
  });

  it("shows a Chinese failure and does not claim the retry succeeded", async () => {
    renderProblem();
    const original = requestMock.getMockImplementation()!;
    requestMock.mockImplementation((arg) =>
      arg.method === "POST"
        ? Promise.reject(new Error("INTERNAL_ERROR"))
        : original(arg),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "查看 Raw 诊断" }),
    );
    const dialog = await screen.findByRole("dialog");
    const retryButton = await within(dialog).findByRole("button", {
      name: "重新质检并继续处理",
    });
    await waitFor(() => expect(retryButton).toBeEnabled());
    fireEvent.click(retryButton);
    expect(
      await within(dialog).findByText("重新处理未能提交，请刷新状态后重试。"),
    ).toBeVisible();
    expect(dialog).not.toHaveTextContent("INTERNAL_ERROR");
    expect(
      within(dialog).queryByText("已排队，等待重新处理"),
    ).not.toBeInTheDocument();
  });
});
