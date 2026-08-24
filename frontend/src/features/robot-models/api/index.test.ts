import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  authorizeRobotModelAssetDownload,
  bindRobotModelVersion,
  listRobotModelBindings,
  listRobotModelJointMappings,
  listRobotModelAssets,
  loadRobotBindingTarget,
  preflightRobotModelPublish,
  uploadRobotModelAssets,
} from ".";

const scope = {
  organizationId: "org-assets",
  projectId: "project-assets",
  regionCode: "region-assets",
} as const;

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p14-assets-api-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "p14-manager", displayName: "P14 管理员", roleIds: [] },
      "p14-session-token",
    );
  useShellStore.getState().setScope(scope);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("P14 robot model asset client", () => {
  it("lists an exact version-scoped manifest with no-store transport", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [
          {
            asset_id: "asset-1",
            relative_path: "models/xr-01.urdf",
            role: "URDF",
            media_type: "application/xml",
            size_bytes: 42,
            sha256: "a".repeat(64),
            created_at: "2026-08-19T11:00:00Z",
          },
        ],
        scope: {
          organization_id: scope.organizationId,
          project_id: scope.projectId,
        },
        request_id: "p14-assets-list",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listRobotModelAssets(scope.organizationId, "version-1"),
    ).resolves.toEqual([
      expect.objectContaining({ asset_id: "asset-1", role: "URDF" }),
    ]);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/organizations/org-assets/robot-model-versions/version-1/assets",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("GET");
    expect(init.cache).toBe("no-store");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer p14-session-token");
    expect(headers.get("X-Organization-Id")).toBe(scope.organizationId);
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
  });

  it("obtains a fresh download grant without accepting physical object locators", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        asset_id: "asset-1",
        download_url: "https://object.example.test/grant?signature=ephemeral",
        expires_at: "2026-08-19T11:15:00Z",
        sha256: "b".repeat(64),
        media_type: "application/xml",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      authorizeRobotModelAssetDownload(
        scope.organizationId,
        "version-1",
        "asset-1",
      ),
    ).resolves.toMatchObject({ asset_id: "asset-1" });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.cache).toBe("no-store");

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        asset_id: "asset-1",
        download_url: "https://object.example.test/grant?signature=ephemeral",
        expires_at: "2026-08-19T11:15:00Z",
        sha256: "b".repeat(64),
        media_type: "application/xml",
        object_key: "must-not-cross-the-api-boundary",
      }),
    );
    await expect(
      authorizeRobotModelAssetDownload(
        scope.organizationId,
        "version-1",
        "asset-1",
      ),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  });

  it("keeps multipart grants out of its result and completes the file with the returned ETag", async () => {
    const createEnvelope = {
      data: {
        upload_id: "upload-1",
        version_id: "version-1",
        status: "UPLOADING",
        files: [
          {
            relative_path: "xr-01.urdf",
            role: "URDF",
            media_type: "application/xml",
            size_bytes: 8,
            sha256: "c".repeat(64),
            status: "UPLOADING",
            part_size_bytes: 16 * 1024 * 1024,
            total_parts: 1,
            part_authorizations: [
              {
                part_number: 1,
                url: "https://object.example.test/short-lived-part-grant",
                expires_at: "2026-08-19T11:15:00Z",
              },
            ],
          },
        ],
        created_at: "2026-08-19T11:00:00Z",
        updated_at: "2026-08-19T11:00:00Z",
        completed_at: null,
      },
      scope: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
      },
      request_id: "p14-assets-upload",
    };
    const completedEnvelope = {
      ...createEnvelope,
      data: {
        ...createEnvelope.data,
        status: "COMPLETED",
        files: createEnvelope.data.files.map((file) => ({
          ...file,
          status: "COMPLETED",
          part_authorizations: [],
        })),
        completed_at: "2026-08-19T11:01:00Z",
      },
    };
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "https://object.example.test/short-lived-part-grant") {
        expect(init).toMatchObject({
          method: "PUT",
          cache: "no-store",
          credentials: "omit",
        });
        return new Response(null, {
          status: 200,
          headers: { ETag: '"part-etag"' },
        });
      }
      if (input.endsWith("/upload-sessions"))
        return jsonResponse(createEnvelope);
      if (input.endsWith(":complete-file"))
        return jsonResponse(completedEnvelope);
      throw new Error(`unexpected request ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["robot-v1"], "xr-01.urdf", {
      type: "application/xml",
    });

    await expect(
      uploadRobotModelAssets(
        scope.organizationId,
        "version-1",
        [
          {
            file,
            relativePath: "xr-01.urdf",
            role: "URDF",
            mediaType: "application/xml",
            sha256: "c".repeat(64),
          },
        ],
        "p14-upload-key",
      ),
    ).resolves.toBe("upload-1");

    const completeCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith(":complete-file"),
    );
    expect(JSON.parse(String((completeCall?.[1] as RequestInit).body))).toEqual(
      {
        relative_path: "xr-01.urdf",
        parts: [{ part_number: 1, etag: "part-etag" }],
      },
    );
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain(
      "multipart_upload_id",
    );
  });

  it("uses the version-scoped mapping and publish-preflight contracts without invented fields", async () => {
    const mappingResponse = {
      items: [
        {
          source_joint_name: "joint_1",
          target_joint_name: "actuator_1",
          direction: "SAME",
        },
      ],
      mapping_hash: "d".repeat(64),
      scope: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
      },
      request_id: "p14-mappings",
    };
    const preflightResponse = {
      data: {
        allowed: true,
        preflight_token: "signed-preflight-token".repeat(4),
        expires_at: "2026-08-19T11:15:00Z",
        expected_etag: '"registry:version-1:2"',
        asset_manifest_hash: "a".repeat(64),
        mapping_hash: "d".repeat(64),
        checks: [
          {
            code: "URDF_WELL_FORMED",
            passed: true,
            message: "The URDF structure and joint declarations are valid.",
          },
        ],
        blockers: [],
      },
      scope: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
      },
      request_id: "p14-preflight",
    };
    const fetchMock = vi.fn(async (input: string, _init?: RequestInit) => {
      if (input.endsWith("/joint-mappings"))
        return jsonResponse(mappingResponse);
      if (input.endsWith(":preflight-publish"))
        return jsonResponse(preflightResponse);
      throw new Error(`unexpected request ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listRobotModelJointMappings(scope.organizationId, "version-1"),
    ).resolves.toEqual(mappingResponse.items);

    await expect(
      preflightRobotModelPublish(scope.organizationId, {
        versionId: "version-1",
        etag: '"registry:version-1:2"',
        idempotencyKey: "p14-preflight-key",
      }),
    ).resolves.toMatchObject({ allowed: true });

    const preflightCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith(":preflight-publish"),
    );
    const init = preflightCall?.[1] as RequestInit;
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("If-Match")).toBe('"registry:version-1:2"');
    expect(headers.get("Idempotency-Key")).toBe("p14-preflight-key");
  });

  it("loads a current robot ETag and records a version-scoped binding without client-supplied facts", async () => {
    const binding = {
      binding_id: "binding-1",
      robot_id: "robot-1",
      region_code: scope.regionCode,
      version_id: "version-1",
      status: "ACTIVE",
      bound_at: "2026-08-19T11:00:00Z",
      unbound_at: null,
    };
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input.endsWith("/robots/robot-1/bootstrap")) {
        return jsonResponse({
          data: {
            robot: {
              id: "robot-1",
              display_name: "XR-01",
              serial_no: "XR-01-SN",
            },
            etag: '"robot-1:2"',
          },
        });
      }
      if (input.endsWith("/bindings") && init?.method === "POST") {
        return jsonResponse(binding);
      }
      if (input.endsWith("/bindings")) {
        return jsonResponse({
          items: [binding],
          scope: {
            organization_id: scope.organizationId,
            project_id: scope.projectId,
          },
          request_id: "p14-bindings-list",
        });
      }
      throw new Error(`unexpected request ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      loadRobotBindingTarget({
        projectId: scope.projectId,
        regionCode: scope.regionCode,
        robotId: "robot-1",
      }),
    ).resolves.toEqual({
      robotId: "robot-1",
      displayName: "XR-01",
      etag: '"robot-1:2"',
    });
    await expect(
      listRobotModelBindings(scope.organizationId, "version-1"),
    ).resolves.toEqual([binding]);
    await expect(
      bindRobotModelVersion(scope.organizationId, {
        versionId: "version-1",
        regionCode: scope.regionCode,
        robotId: "robot-1",
        robotEtag: '"robot-1:2"',
        idempotencyKey: "binding-key",
      }),
    ).resolves.toEqual(binding);
    const bindCall = fetchMock.mock.calls.at(-1);
    const init = bindCall?.[1] as RequestInit;
    expect(bindCall?.[0]).toBe(
      "/api/v1/organizations/org-assets/robot-model-versions/version-1/bindings",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      region_code: scope.regionCode,
      robot_id: "robot-1",
      robot_etag: '"robot-1:2"',
    });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(
      "binding-key",
    );
  });
});
