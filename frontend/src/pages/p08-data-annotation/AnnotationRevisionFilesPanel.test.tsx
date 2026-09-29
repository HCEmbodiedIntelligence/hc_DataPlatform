// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnnotationRevisionFilesPanel } from "./AnnotationRevisionFilesPanel";
import {
  downloadRevisionFile,
  loadRevisionFile,
  requestRevisionFile,
  type RevisionFile,
} from "./revision-files";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

vi.mock("./revision-files", () => ({
  loadRevisionFile: vi.fn(),
  requestRevisionFile: vi.fn(),
  downloadRevisionFile: vi.fn(),
}));
const load = vi.mocked(loadRevisionFile),
  retry = vi.mocked(requestRevisionFile),
  download = vi.mocked(downloadRevisionFile);
beforeEach(() => {
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:annotation-file"),
    revokeObjectURL: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function setup(
  status: RevisionFile["status"],
  options = { approved: false, canRetry: true, canDownload: true },
) {
  const bundle = createVisualAnnotationBundle({ mode: "annotation" });
  const revision = bundle.history.revisions.at(-1)!;
  const history = {
    ...bundle.history,
    task: {
      ...bundle.task,
      approved_revision: options.approved ? revision.revision : null,
    },
  };
  const file: RevisionFile = {
    task_id: revision.task_id,
    revision: revision.revision,
    revision_content_hash: revision.content_hash,
    format_version: "hc-annotation/1",
    status,
    generation: 1,
    attempts: 1,
    content_sha256: "a".repeat(64),
    size_bytes: 100,
    error_code:
      status === "FAILED" ? "ANNOTATION_FILE_GENERATION_FAILED" : null,
    updated_at: "2026-09-29T00:00:00Z",
  };
  load.mockResolvedValue(file);
  download.mockResolvedValue(new Blob(["verified fixture bytes"]));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <AnnotationRevisionFilesPanel
        scope={visualAnnotationScope}
        history={history}
        dirty
        canDownload={options.canDownload}
        canRetry={options.canRetry}
      />
    </QueryClientProvider>,
  );
  return { ...view, revision, file };
}

describe("versioned annotation file actions", () => {
  it("shows saved revision separately from a failed file and retries that generation", async () => {
    const { revision, file } = setup("FAILED");
    expect(screen.getByText(/已保存。文件生成状态单独显示/)).toBeVisible();
    expect(
      await screen.findByText(/ANNOTATION_FILE_GENERATION_FAILED/),
    ).toBeVisible();
    expect(retry).not.toHaveBeenCalled();
    retry.mockResolvedValue({ ...file, status: "PENDING", generation: 2 });
    load.mockResolvedValue({ ...file, status: "PENDING", generation: 2 });
    await userEvent.click(screen.getByRole("button", { name: "重试生成" }));
    await waitFor(() =>
      expect(retry).toHaveBeenCalledWith(visualAnnotationScope, revision, 1),
    );
    expect(await screen.findByText("等待生成")).toBeVisible();
    expect(download).not.toHaveBeenCalled();
  });

  it("requires an explicit unreviewed choice and downloads the selected saved revision", async () => {
    const { revision, file } = setup("READY");
    await screen.findByText("文件已就绪");
    const button = screen.getByRole("button", { name: /下载未审核/ });
    expect(button).toBeDisabled();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /明确下载未审核/ }),
    );
    await userEvent.click(button);
    expect(download).toHaveBeenCalledWith(
      visualAnnotationScope,
      revision,
      file,
      true,
      expect.any(AbortSignal),
    );
    expect(retry).not.toHaveBeenCalled();
    expect(screen.getByText("当前未保存的修改不包含在文件中。")).toBeVisible();
  });

  it("defaults to the current approved revision", async () => {
    const { revision, file } = setup("READY", {
      approved: true,
      canRetry: true,
      canDownload: true,
    });
    await screen.findByText("文件已就绪");
    expect(screen.queryByRole("checkbox")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /下载已审核/ }));
    expect(download).toHaveBeenCalledWith(
      visualAnnotationScope,
      revision,
      file,
      false,
      expect.any(AbortSignal),
    );
  });

  it("keeps retry and download disabled without their capabilities", async () => {
    setup("FAILED", { approved: false, canRetry: false, canDownload: false });
    await screen.findByText(/ANNOTATION_FILE_GENERATION_FAILED/);
    expect(screen.getByRole("button", { name: "重试生成" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: /下载未审核/ })).toBeDisabled();
    expect(retry).not.toHaveBeenCalled();
  });

  it("cancels an unfinished download when the panel closes", async () => {
    const { unmount } = setup("READY", {
      approved: true,
      canRetry: true,
      canDownload: true,
    });
    download.mockReturnValue(new Promise(() => {}));
    await screen.findByText("文件已就绪");
    await userEvent.click(screen.getByRole("button", { name: /下载已审核/ }));
    const signal = download.mock.calls[0]![4]!;
    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
