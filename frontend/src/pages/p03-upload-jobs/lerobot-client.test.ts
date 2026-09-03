// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import type { LeRobotFolderSelection } from "./upload-contract";
import {
  buildLeRobotImportManifest,
  LEROBOT_MULTIPART_BYTES,
  LeRobotUploadFailure,
  MAX_SAME_ORIGIN_PROXY_PARTS,
  uploadNativeLeRobot,
  type LeRobotImportAccepted,
  type LeRobotUploadProgress,
} from "./lerobot-client";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("../../shared/api/http-client", () => ({ request: requestMock }));

const scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "cn-hz",
};
const binding = {
  datasetId: "dataset-a",
  collectionTaskId: "task-a",
  robotId: "robot-a",
};
const importId = "a".repeat(32);
const root = "/projects/project-a/regions/cn-hz/lerobot-imports";

function oneFileSelection(): LeRobotFolderSelection {
  const file = new File(["raw"], "info.json");
  return {
    format: "lerobot",
    version: "v3.0",
    robotType: "unitree_g1",
    rootDirectory: "source",
    info: { total_episodes: 1 },
    episodeCount: 1,
    sourceFiles: [{ file, path: "meta/info.json" }],
    sourceBytes: file.size,
  };
}

function accepted(): LeRobotImportAccepted {
  return {
    schema_version: "lerobot-web-import-accepted/v1",
    import_id: importId,
    status: "EPISODES_QUEUED",
    episode_count: 1,
    source_file_count: 1,
    episode_task_count: 1,
    episode_plan_key: "derived/plan.json",
  };
}

class FailingDirectUpload {
  status = 0;
  timeout = 0;
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  ontimeout: (() => void) | null = null;

  open(): void {}

  send(): void {
    this.onerror?.();
  }
}

describe("native LeRobot upload contract", () => {
  beforeEach(() => {
    requestMock.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("declares original files and never invents an MCAP Raw", () => {
    const info = {
      codebase_version: "v3.0",
      robot_type: "unitree_g1",
      total_episodes: 1,
    };
    const video = new File(["video"], "file.mp4");
    Object.defineProperty(video, "size", {
      value: LEROBOT_MULTIPART_BYTES + 1,
    });
    const sourceFiles = [
      {
        file: new File([JSON.stringify(info)], "info.json"),
        path: "meta/info.json",
      },
      { file: new File(["meta"], "tasks.jsonl"), path: "meta/tasks.jsonl" },
      { file: video, path: "videos/camera/chunk-000/file-000.mp4" },
    ];
    const selection: LeRobotFolderSelection = {
      format: "lerobot",
      version: "v3.0",
      robotType: "unitree_g1",
      rootDirectory: "source",
      info,
      episodeCount: 1,
      sourceFiles,
      sourceBytes: sourceFiles.reduce(
        (total, item) => total + item.file.size,
        0,
      ),
    };

    const manifest = buildLeRobotImportManifest(selection, {
      datasetId: "dataset-a",
      collectionTaskId: "task-a",
      robotId: "robot-a",
    });

    expect(manifest.files.map((item) => item.path)).toEqual(
      sourceFiles.map((item) => item.path),
    );
    expect(manifest.files[2]?.part_count).toBe(2);
    expect(manifest.files.some((item) => item.path.endsWith(".mcap"))).toBe(
      false,
    );
  });

  it("falls back to the scoped platform route when object-store direct PUT fails", async () => {
    vi.stubGlobal(
      "XMLHttpRequest",
      FailingDirectUpload as unknown as typeof XMLHttpRequest,
    );
    requestMock.mockImplementation(
      async (options: { method: string; path: string }) => {
        if (options.method === "POST" && options.path === root) {
          return {
            schema_version: "lerobot-web-import-grant/v1",
            import_id: importId,
            assets: [
              {
                path: "meta/info.json",
                multipart_upload_id: "upload-a",
                parts: [],
                completed: false,
              },
            ],
          };
        }
        if (options.path.endsWith("assets:authorize-parts")) {
          return {
            path: "meta/info.json",
            completed: false,
            parts: [
              {
                part_number: 1,
                url: "https://oss.invalid/direct-part",
                expires_at: "2026-09-01T10:00:00Z",
              },
            ],
          };
        }
        if (options.method === "PUT") return undefined;
        if (options.path.endsWith("assets:complete")) return undefined;
        if (options.path.endsWith(":commit")) return accepted();
        throw new Error(`Unexpected request ${options.method} ${options.path}`);
      },
    );
    const progress: LeRobotUploadProgress[] = [];

    await expect(
      uploadNativeLeRobot(scope, oneFileSelection(), binding, (value) =>
        progress.push(value),
      ),
    ).resolves.toEqual(accepted());

    const proxyCall = requestMock.mock.calls.find(
      ([options]) => (options as { method: string }).method === "PUT",
    )?.[0] as
      | {
          path: string;
          query: Record<string, unknown>;
          binaryBody: Blob;
        }
      | undefined;
    expect(proxyCall?.path).toBe(`${root}/${importId}/assets:upload-part`);
    expect(proxyCall?.query).toEqual({
      datasetId: "dataset-a",
      path: "meta/info.json",
      multipartUploadId: "upload-a",
      partNumber: 1,
    });
    expect(proxyCall?.binaryBody.size).toBe(3);
    expect(progress.some((value) => value.transferMode === "proxy")).toBe(true);
  });

  it("automatically retries retryable platform proxy failures", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "XMLHttpRequest",
      FailingDirectUpload as unknown as typeof XMLHttpRequest,
    );
    let proxyAttempts = 0;
    requestMock.mockImplementation(
      async (options: { method: string; path: string }) => {
        if (options.method === "POST" && options.path === root) {
          return {
            schema_version: "lerobot-web-import-grant/v1",
            import_id: importId,
            assets: [
              {
                path: "meta/info.json",
                multipart_upload_id: "upload-a",
                parts: [],
                completed: false,
              },
            ],
          };
        }
        if (options.path.endsWith("assets:authorize-parts")) {
          return {
            path: "meta/info.json",
            completed: false,
            parts: [
              {
                part_number: 1,
                url: "https://oss.invalid/direct-part",
                expires_at: "2026-09-01T10:00:00Z",
              },
            ],
          };
        }
        if (options.method === "PUT") {
          proxyAttempts += 1;
          if (proxyAttempts < 3) {
            throw createDomainError({
              code: "SERVER_ERROR",
              problemCode: "INTERNAL_SERVER_ERROR",
              message: "The server could not complete the request.",
              fieldErrors: [],
              operationErrors: [],
              blockedReasons: [],
              requestId: `request-${proxyAttempts}`,
              retryable: true,
              httpStatus: 500,
            });
          }
          return undefined;
        }
        if (options.path.endsWith("assets:complete")) return undefined;
        if (options.path.endsWith(":commit")) return accepted();
        throw new Error(`Unexpected request ${options.method} ${options.path}`);
      },
    );

    const upload = uploadNativeLeRobot(
      scope,
      oneFileSelection(),
      binding,
      () => {},
    );
    await vi.runAllTimersAsync();

    await expect(upload).resolves.toEqual(accepted());
    expect(proxyAttempts).toBe(3);
  });

  it("skips parts already persisted in the resumed server session", async () => {
    requestMock.mockImplementation(
      async (options: { method: string; path: string }) => {
        if (options.method === "POST" && options.path === root) {
          return {
            schema_version: "lerobot-web-import-grant/v1",
            import_id: importId,
            assets: [
              {
                path: "meta/info.json",
                multipart_upload_id: "upload-a",
                parts: [],
                completed: false,
              },
            ],
          };
        }
        if (options.path.endsWith("assets:authorize-parts")) {
          return {
            path: "meta/info.json",
            completed: false,
            uploaded_part_numbers: [1],
            parts: [],
          };
        }
        if (options.path.endsWith("assets:complete")) return undefined;
        if (options.path.endsWith(":commit")) return accepted();
        throw new Error(`Unexpected request ${options.method} ${options.path}`);
      },
    );
    const progress: LeRobotUploadProgress[] = [];

    await expect(
      uploadNativeLeRobot(scope, oneFileSelection(), binding, (value) =>
        progress.push(value),
      ),
    ).resolves.toEqual(accepted());

    expect(
      requestMock.mock.calls.filter(
        ([options]) => (options as { method: string }).method === "PUT",
      ),
    ).toHaveLength(0);
    expect(progress.some((value) => value.uploadedBytes === 3)).toBe(true);
  });

  it("reserves same-origin request capacity while proxying multipart uploads", async () => {
    vi.stubGlobal(
      "XMLHttpRequest",
      FailingDirectUpload as unknown as typeof XMLHttpRequest,
    );
    const file = new File(["raw"], "video.mp4");
    Object.defineProperty(file, "size", {
      value: LEROBOT_MULTIPART_BYTES * 4,
    });
    const selection: LeRobotFolderSelection = {
      ...oneFileSelection(),
      sourceFiles: [{ file, path: "videos/camera/file.mp4" }],
      sourceBytes: file.size,
    };
    let activeProxyRequests = 0;
    let maximumActiveProxyRequests = 0;
    let proxyRequestCount = 0;

    requestMock.mockImplementation(
      async (options: {
        method: string;
        path: string;
        body?: { part_numbers?: number[] };
      }) => {
        if (options.method === "POST" && options.path === root) {
          return {
            schema_version: "lerobot-web-import-grant/v1",
            import_id: importId,
            assets: [
              {
                path: "videos/camera/file.mp4",
                multipart_upload_id: "upload-a",
                parts: [],
                completed: false,
              },
            ],
          };
        }
        if (options.path.endsWith("assets:authorize-parts")) {
          return {
            path: "videos/camera/file.mp4",
            completed: false,
            parts: (options.body?.part_numbers ?? []).map((partNumber) => ({
              part_number: partNumber,
              url: `https://oss.invalid/direct-part-${partNumber}`,
              expires_at: "2026-09-01T10:00:00Z",
            })),
          };
        }
        if (options.method === "PUT") {
          proxyRequestCount += 1;
          activeProxyRequests += 1;
          maximumActiveProxyRequests = Math.max(
            maximumActiveProxyRequests,
            activeProxyRequests,
          );
          await new Promise<void>((resolve) => queueMicrotask(resolve));
          activeProxyRequests -= 1;
          return undefined;
        }
        if (options.path.endsWith("assets:complete")) return undefined;
        if (options.path.endsWith(":commit")) return accepted();
        throw new Error(`Unexpected request ${options.method} ${options.path}`);
      },
    );

    await expect(
      uploadNativeLeRobot(scope, selection, binding, () => {}),
    ).resolves.toEqual(accepted());

    expect(proxyRequestCount).toBe(4);
    expect(maximumActiveProxyRequests).toBe(MAX_SAME_ORIGIN_PROXY_PARTS);
  });

  it("keeps the import session for retry and skips an already completed asset", async () => {
    vi.stubGlobal(
      "XMLHttpRequest",
      FailingDirectUpload as unknown as typeof XMLHttpRequest,
    );
    let proxyShouldFail = true;
    requestMock.mockImplementation(
      async (options: { method: string; path: string }) => {
        if (options.method === "POST" && options.path === root) {
          return {
            schema_version: "lerobot-web-import-grant/v1",
            import_id: importId,
            assets: [
              {
                path: "meta/info.json",
                multipart_upload_id: "upload-a",
                parts: [],
                completed: false,
              },
            ],
          };
        }
        if (options.path.endsWith("assets:authorize-parts")) {
          if (!proxyShouldFail)
            return { path: "meta/info.json", completed: true, parts: [] };
          return {
            path: "meta/info.json",
            completed: false,
            parts: [
              {
                part_number: 1,
                url: "https://oss.invalid/direct-part",
                expires_at: "2026-09-01T10:00:00Z",
              },
            ],
          };
        }
        if (options.method === "PUT") throw new Error("platform unavailable");
        if (options.path.endsWith(":commit")) return accepted();
        throw new Error(`Unexpected request ${options.method} ${options.path}`);
      },
    );

    let failure: unknown;
    try {
      await uploadNativeLeRobot(scope, oneFileSelection(), binding, () => {});
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(LeRobotUploadFailure);
    if (!(failure instanceof LeRobotUploadFailure)) return;
    expect(failure.resume).toMatchObject({
      importId,
      transferMode: "proxy",
    });

    proxyShouldFail = false;
    await expect(
      uploadNativeLeRobot(
        scope,
        oneFileSelection(),
        binding,
        () => {},
        failure.resume,
      ),
    ).resolves.toEqual(accepted());
    expect(
      requestMock.mock.calls.filter(
        ([options]) =>
          (options as { method: string; path: string }).method === "POST" &&
          (options as { path: string }).path === root,
      ),
    ).toHaveLength(1);
  });
});
