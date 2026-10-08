import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import { detectLeRobotCapabilities } from "./lerobot-capabilities";
vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "local",
};
const supported = {
  schema_version: "lerobot-capabilities/v1",
  capability_revision: "profile/future-17",
  info_sha256: "a".repeat(64),
  validation_scope: "DECLARED_METADATA",
  storage_supported: true,
  processing_supported: true,
  profile_id: "float64",
  output_storage_format: "lerobot_v3",
  reasons: [],
};
beforeEach(() => {
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "uploader", displayName: "Uploader", roleIds: [] },
    "test-session",
  );
  store.setSessionScopes(
    [
      {
        ...scope,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: ["upload.manage"],
      },
    ],
    1,
  );
  store.setScope(scope);
});
afterEach(() => {
  vi.resetAllMocks();
  useShellStore.getState().clearSensitiveState();
});
it("preserves server format decisions and revisions without a local dtype list", async () => {
  const info = {
    features: { action: { dtype: "float64", shape: [2], names: ["x", "y"] } },
  };
  mocked.mockResolvedValueOnce(supported as never);
  await expect(detectLeRobotCapabilities(scope, info)).resolves.toEqual(
    supported,
  );
  expect(mocked).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/projects/project/regions/local/lerobot-imports:capabilities",
      scope,
      scopeMode: "explicit",
      cache: "no-store",
      body: { info },
    }),
  );
});
it.each([
  { ...supported, storage_supported: false },
  { ...supported, profile_id: null },
  { ...supported, info_sha256: "invalid" },
  { ...supported, validation_scope: "ALL_FILES_VERIFIED" },
])(
  "rejects a contradictory or malformed capability response",
  async (response) => {
    mocked.mockResolvedValueOnce(response as never);
    await expect(detectLeRobotCapabilities(scope, {})).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  },
);
it.each(["logout", "revoke", "revision"])(
  "discards a late capability response after %s",
  async (change) => {
    let complete!: (value: unknown) => void;
    mocked.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const pending = detectLeRobotCapabilities(scope, {});
    const rejection =
      change === "revision"
        ? expect(pending).rejects.toMatchObject({ name: "AbortError" })
        : expect(pending).rejects.toBe("session-or-grant-changed");
    if (change === "logout") useShellStore.getState().clearSensitiveState();
    else if (change === "revoke")
      useShellStore.getState().setSessionScopes([], 2);
    else
      useShellStore.getState().setSessionScopes(
        [
          {
            ...scope,
            regionCodes: [scope.regionCode],
            projectWide: false,
            capabilities: ["upload.manage"],
          },
        ],
        2,
      );
    complete(supported);
    await rejection;
  },
);
