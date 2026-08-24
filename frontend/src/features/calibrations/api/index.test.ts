import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  createCalibrationSet,
  getCalibrationValidationReport,
  getCalibrationVersionDocument,
  associateCalibrationDatasetVersion,
  listCalibrationDatasetAssociations,
  listCalibrationVersions,
  preflightCalibrationPublish,
  publishCalibration,
  recalibrateCalibrationSet,
  validateCalibrationVersion,
  type CreateCalibrationSetInput,
} from ".";

const scope = {
  projectId: "project-calibrations",
  regionCode: "cn-shanghai-1",
} as const;

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function calibrationEnvelope() {
  return {
    data: {
      id: "set-1",
      robot_instance_id: "robot-1",
      component_id: "camera-1",
      version: "1",
      snapshot_status: "READY",
      availability: "ACTIVE",
      content_hash: "a".repeat(64),
      validation_context_hash: "b".repeat(64),
      validation: {
        status: "PASSED",
        content_hash: "a".repeat(64),
        validation_context_hash: "b".repeat(64),
        report_id: "report-1",
      },
      etag: '"calibration:set-1:2"',
      allowed_actions: ["VIEW"],
      blocked_reasons: [],
    },
    scope: { project_id: scope.projectId, region_code: scope.regionCode },
    request_id: "p16-client-test",
    contract_version: "2026-08-21",
  };
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p16-calibrations-api-test",
    releaseEnv: "test",
  });
  useShellStore.getState().setSession(
    {
      actorId: "calibration-publisher",
      displayName: "标定发布者",
      roleIds: [],
    },
    "p16-calibrations-session",
  );
  useShellStore.getState().setScope({
    organizationId: "org-calibrations",
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("P16 calibration publication client", () => {
  it("uses one scoped idempotency key for durable preflight and publish", async () => {
    const fetchMock = vi
      .fn((_input: string, _init?: RequestInit) => jsonResponse({}))
      .mockResolvedValueOnce(
        jsonResponse({
          data: {
            allowed: true,
            preflight_token: "p".repeat(64),
            expires_at: "2026-08-21T08:05:00Z",
            resource_revision: '"calibration:set-1:1"',
            impacts: [
              {
                code: "CALIBRATION_READY_FOR_PUBLISH",
                message: "已绑定校验事实。",
              },
            ],
            warnings: [],
            blockers: [],
          },
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "p16-preflight",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(calibrationEnvelope()));
    vi.stubGlobal("fetch", fetchMock);

    const preflight = await preflightCalibrationPublish(scope, {
      setId: "set-1",
      version: "1",
      etag: '"calibration:set-1:1"',
      expectedHash: "a".repeat(64),
      validationContextHash: "b".repeat(64),
      validationReportId: "report-1",
      changeSummary: "P16 客户端发布",
      idempotencyKey: "p16-publish-key",
    });
    expect(preflight.allowed).toBe(true);
    await expect(
      publishCalibration(scope, {
        setId: "set-1",
        version: "1",
        etag: '"calibration:set-1:1"',
        idempotencyKey: "p16-publish-key",
        preflightToken: "p".repeat(64),
      }),
    ).resolves.toMatchObject({
      snapshotStatus: "READY",
      availability: "ACTIVE",
    });

    const preflightCall = fetchMock.mock.calls[0];
    if (!preflightCall) throw new Error("missing preflight request");
    const [preflightUrl, preflightInit] = preflightCall;
    expect(preflightUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions/1:preflight-publish",
    );
    expect(JSON.parse(String(preflightInit?.body))).toEqual({
      expected_hash: "a".repeat(64),
      expected_etag: '"calibration:set-1:1"',
      validation_report_id: "report-1",
      compatibility_check_id: null,
      change_summary: "P16 客户端发布",
      acknowledge_warning_codes: [],
      validation_context_hash: "b".repeat(64),
    });
    expect(new Headers(preflightInit?.headers).get("If-Match")).toBe(
      '"calibration:set-1:1"',
    );
    expect(new Headers(preflightInit?.headers).get("Idempotency-Key")).toBe(
      "p16-publish-key",
    );

    const publishCall = fetchMock.mock.calls[1];
    if (!publishCall) throw new Error("missing publish request");
    const [publishUrl, publishInit] = publishCall;
    expect(publishUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions/1:publish",
    );
    expect(JSON.parse(String(publishInit?.body))).toEqual({
      preflight_token: "p".repeat(64),
    });
    expect(new Headers(publishInit?.headers).get("Authorization")).toBe(
      "Bearer p16-calibrations-session",
    );
  });

  it("creates, reads and validates a real version document with scoped headers", async () => {
    const document: CreateCalibrationSetInput["document"] = {
      frame_transforms: [
        {
          parent_frame: "base_link",
          child_frame: "camera_front",
          translation_m: [0.12, 0, 0.42],
          quaternion_xyzw: [0, 0, 0, 1],
          covariance: null,
        },
      ],
      camera_intrinsics: [
        {
          frame_id: "camera_front",
          width_px: 1920,
          height_px: 1080,
          fx_px: 1010,
          fy_px: 1008,
          cx_px: 960,
          cy_px: 540,
          distortion: [],
        },
      ],
    };
    const report = {
      id: "calibration-report-1",
      set_id: "set-1",
      version: "1",
      content_hash: "a".repeat(64),
      validation_context_hash: "b".repeat(64),
      status: "PASSED",
      findings: [],
      checked_by: "calibration-publisher",
      checked_at: "2026-08-21T08:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(calibrationEnvelope()))
      .mockResolvedValueOnce(
        jsonResponse({
          data: {
            set_id: "set-1",
            version: "1",
            source: "IMPORT",
            content_hash: "a".repeat(64),
            document,
            created_by: "calibration-publisher",
            created_at: "2026-08-21T08:00:00Z",
          },
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "document-read",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          data: report,
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "validate",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          data: report,
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "report-read",
          contract_version: "2026-08-21",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createCalibrationSet(
        scope,
        {
          set_id: "set-1",
          robot_instance_id: "robot-1",
          component_id: "camera-1",
          source: "IMPORT",
          document,
        },
        "p16-create-key",
      ),
    ).resolves.toMatchObject({ id: "set-1", contentHash: "a".repeat(64) });
    await expect(
      getCalibrationVersionDocument(scope, "set-1", "1"),
    ).resolves.toMatchObject({
      document,
    });
    await expect(
      validateCalibrationVersion(scope, {
        setId: "set-1",
        version: "1",
        etag: '"calibration:set-1:2"',
        idempotencyKey: "p16-validate-key",
      }),
    ).resolves.toMatchObject({ id: "calibration-report-1", status: "PASSED" });
    await expect(
      getCalibrationValidationReport(scope, "calibration-report-1"),
    ).resolves.toEqual(report);

    const [createUrl, createInit] = fetchMock.mock.calls[0] ?? [];
    expect(createUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets",
    );
    expect(JSON.parse(String(createInit?.body))).toEqual({
      set_id: "set-1",
      robot_instance_id: "robot-1",
      component_id: "camera-1",
      source: "IMPORT",
      document,
    });
    expect(new Headers(createInit?.headers).get("Idempotency-Key")).toBe(
      "p16-create-key",
    );
    const [validateUrl, validateInit] = fetchMock.mock.calls[2] ?? [];
    expect(validateUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions/1:validate",
    );
    expect(validateInit?.body).toBeUndefined();
    expect(new Headers(validateInit?.headers).get("If-Match")).toBe(
      '"calibration:set-1:2"',
    );
    expect(new Headers(validateInit?.headers).get("Idempotency-Key")).toBe(
      "p16-validate-key",
    );
  });

  it("lists immutable versions and creates a scoped recalibration successor", async () => {
    const document: CreateCalibrationSetInput["document"] = {
      frame_transforms: [
        {
          parent_frame: "base_link",
          child_frame: "camera_front",
          translation_m: [0.15, 0, 0.42],
          quaternion_xyzw: [0, 0, 0, 1],
          covariance: null,
        },
      ],
      camera_intrinsics: [],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              version: "2",
              source: "RECALIBRATION",
              content_hash: "c".repeat(64),
              created_by: "calibration-publisher",
              created_at: "2026-08-21T08:05:00Z",
            },
            {
              version: "1",
              source: "IMPORT",
              content_hash: "a".repeat(64),
              created_by: "calibration-publisher",
              created_at: "2026-08-21T08:00:00Z",
            },
          ],
          page_info: {
            has_next_page: false,
            has_previous_page: false,
            start_cursor: null,
            end_cursor: null,
          },
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "version-list",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          ...calibrationEnvelope(),
          data: {
            ...calibrationEnvelope().data,
            version: "2",
            snapshot_status: "DRAFT",
            availability: null,
            content_hash: "c".repeat(64),
            validation_context_hash: null,
            validation: null,
            etag: '"calibration:set-1:3"',
          },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listCalibrationVersions(scope, "set-1")).resolves.toEqual([
      expect.objectContaining({ version: "2", source: "RECALIBRATION" }),
      expect.objectContaining({ version: "1", source: "IMPORT" }),
    ]);
    await expect(
      recalibrateCalibrationSet(
        scope,
        "set-1",
        '"calibration:set-1:2"',
        {
          document,
          change_summary: "更换相机支架后的复测。",
        },
        "p16-recalibrate-key",
      ),
    ).resolves.toMatchObject({ version: "2", snapshotStatus: "DRAFT" });

    const [historyUrl, historyInit] = fetchMock.mock.calls[0] ?? [];
    expect(historyUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions",
    );
    expect(historyInit?.method).toBe("GET");
    const [recalibrateUrl, recalibrateInit] = fetchMock.mock.calls[1] ?? [];
    expect(recalibrateUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions",
    );
    expect(JSON.parse(String(recalibrateInit?.body))).toEqual({
      document,
      change_summary: "更换相机支架后的复测。",
    });
    expect(new Headers(recalibrateInit?.headers).get("If-Match")).toBe(
      '"calibration:set-1:2"',
    );
    expect(new Headers(recalibrateInit?.headers).get("Idempotency-Key")).toBe(
      "p16-recalibrate-key",
    );
  });

  it("lists and creates immutable calibration-to-dataset-version associations", async () => {
    const association = {
      set_id: "set-1",
      calibration_version: "1",
      dataset_id: "dataset_p16client",
      dataset_version_id: "version_p16client",
      associated_by: "calibration-publisher",
      associated_at: "2026-08-21T08:05:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          items: [association],
          page_info: {
            has_next_page: false,
            has_previous_page: false,
            start_cursor: null,
            end_cursor: null,
          },
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "association-list",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          data: association,
          scope: { project_id: scope.projectId, region_code: scope.regionCode },
          request_id: "association-create",
          contract_version: "2026-08-21",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listCalibrationDatasetAssociations(scope, "set-1", "1"),
    ).resolves.toEqual([association]);
    await expect(
      associateCalibrationDatasetVersion(
        scope,
        "set-1",
        "1",
        '"calibration:set-1:2"',
        {
          dataset_id: association.dataset_id,
          dataset_version_id: association.dataset_version_id,
        },
        "p16-association-key",
      ),
    ).resolves.toEqual(association);

    const [listUrl, listInit] = fetchMock.mock.calls[0] ?? [];
    expect(listUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions/1/dataset-associations",
    );
    expect(listInit?.method).toBe("GET");
    const [createUrl, createInit] = fetchMock.mock.calls[1] ?? [];
    expect(createUrl).toBe(
      "/api/v1/projects/project-calibrations/regions/cn-shanghai-1/calibration-sets/set-1/versions/1/dataset-associations",
    );
    expect(JSON.parse(String(createInit?.body))).toEqual({
      dataset_id: association.dataset_id,
      dataset_version_id: association.dataset_version_id,
    });
    expect(new Headers(createInit?.headers).get("If-Match")).toBe(
      '"calibration:set-1:2"',
    );
    expect(new Headers(createInit?.headers).get("Idempotency-Key")).toBe(
      "p16-association-key",
    );
  });
});
