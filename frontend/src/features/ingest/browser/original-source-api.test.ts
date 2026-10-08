import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  authorizeOriginalFile,
  getOriginalFiles,
  getOriginalEpisode,
} from "./original-source-api";
vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "local",
};
beforeEach(() => {
  useShellStore
    .getState()
    .setSession(
      { actorId: "reader", displayName: "Reader", roleIds: [] },
      "test-session",
    );
  useShellStore.getState().setScope(scope);
  useShellStore.getState().setSessionScopes(
    [
      {
        ...scope,
        regionCodes: ["local"],
        projectWide: false,
        capabilities: [],
      },
    ],
    1,
  );
});
afterEach(() => {
  vi.resetAllMocks();
  useShellStore.getState().clearSensitiveState();
});
it("refuses files from another raw source and an episode from another index", async () => {
  mocked.mockResolvedValueOnce({
    import_id: "another",
    source_format: "LEROBOT_V3",
    episode_count: 1,
    files: [],
    total: 0,
    offset: 0,
  } as never);
  await expect(
    getOriginalFiles(scope, "raw", 0, new AbortController().signal),
  ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  mocked.mockResolvedValueOnce({
    episode_index: 1,
    frame_count: 3,
    fps: 30,
    duration_ns: "100000000",
    videos: [],
  } as never);
  await expect(
    getOriginalEpisode(scope, "raw", 0, new AbortController().signal),
  ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
});
it("authorizes an exact original file for download with explicit scope", async () => {
  const grant = {
    path: "nested/source.mp4",
    url: "https://original.invalid/source",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  };
  mocked.mockResolvedValueOnce(grant as never);
  await expect(
    authorizeOriginalFile(scope, "raw", grant.path, true),
  ).resolves.toEqual(grant);
  expect(mocked).toHaveBeenCalledWith(
    expect.objectContaining({
      scope,
      scopeMode: "explicit",
      cache: "no-store",
      query: { path: grant.path, download: true },
    }),
  );
});
it.each(["path", "expiry"])(
  "refuses a %s mismatch before initiating a download",
  async (kind) => {
    mocked.mockResolvedValue({
      path: kind === "path" ? "different.mp4" : "source.mp4",
      url: "https://original.invalid/source",
      expires_at:
        kind === "expiry"
          ? "2020-01-01T00:00:00Z"
          : new Date(Date.now() + 60_000).toISOString(),
    } as never);
    await expect(
      authorizeOriginalFile(scope, "raw", "source.mp4", true),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  },
);
it("discards a grant arriving after logout", async () => {
  mocked.mockImplementation(async (options) => {
    useShellStore.getState().clearSensitiveState();
    expect(options.signal?.aborted).toBe(true);
    return {
      path: "source.mp4",
      url: "https://original.invalid/source",
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    } as never;
  });
  await expect(
    authorizeOriginalFile(scope, "raw", "source.mp4", true),
  ).rejects.toBeDefined();
});
