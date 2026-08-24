import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  associateDataSchemaDatasetReference,
  createStreamSchema,
  listDataSchemaDatasetReferences,
  preflightDataSchemaPublish,
  publishDataSchema,
  updateStreamSchemaDraft,
  validateStreamSchemaVersion,
  type CreateStreamSchemaInput,
} from ".";

const organizationId = "org-schemas";
const projectId = "project-schemas";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function schemaEnvelope(status = "DRAFT", etag = '"schema:1:1"') {
  return {
    data: {
      schema_id: "camera-schema",
      family_id: "camera-family",
      schema_version: "1",
      display_name: "前视图像",
      logical_type: "IMAGE",
      status,
      compatibility_mode: "BACKWARD",
      compatibility_result: null,
      schema_hash: {
        algorithm: "SHA-256",
        canonicalization_version: "schema-c14n-v1",
        value: "a".repeat(64),
      },
      schema_definition: {
        fields: [
          {
            name: "image",
            type: "bytes",
            description: "payload",
            required: true,
          },
        ],
      },
      etag,
      allowed_actions:
        status === "DRAFT" ? ["VIEW", "EDIT", "VALIDATE", "PUBLISH"] : ["VIEW"],
      blocked_reasons: [],
    },
    scope: { organization_id: organizationId },
    request_id: "p17-client-test",
    contract_version: "2026-08-21",
  };
}

const input: CreateStreamSchemaInput = {
  schema_id: "camera-schema",
  family_id: "camera-family",
  display_name: "前视图像",
  logical_type: "IMAGE",
  compatibility_mode: "BACKWARD",
  schema_definition: {
    fields: [
      { name: "image", type: "bytes", description: "payload", required: true },
    ],
  },
  change_summary: "create camera schema",
};

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p17-schema-api-test",
    releaseEnv: "test",
  });
  useShellStore.getState().setSession(
    {
      actorId: "schema-publisher",
      displayName: "Schema 发布者",
      roleIds: [],
    },
    "p17-session",
  );
  useShellStore.getState().setScope({
    organizationId,
    projectId,
    regionCode: "cn-shanghai-1",
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("P17 schema authoring API", () => {
  it("uses generated create/update/validate/preflight/publish contracts with ETag and idempotency", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(schemaEnvelope()))
      .mockResolvedValueOnce(
        jsonResponse(schemaEnvelope("DRAFT", '"schema:1:2"')),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          data: {
            id: "validation-1",
            schema_id: "camera-schema",
            schema_version: "1",
            content_hash: "a".repeat(64),
            compatibility_check_id: "compatibility-1",
            compatibility_result: "PASSED",
            status: "PASSED",
            findings: [],
            checked_by: "schema-publisher",
            checked_at: "2026-08-21T08:00:00Z",
          },
          scope: { organization_id: organizationId },
          request_id: "validation",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          data: {
            allowed: true,
            preflight_token: "p".repeat(64),
            expires_at: "2026-08-21T08:05:00Z",
            resource_revision: '"schema:1:2"',
            impacts: [],
            warnings: [],
            blockers: [],
          },
          scope: { organization_id: organizationId },
          request_id: "preflight",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(schemaEnvelope("PUBLISHED", '"schema:1:3"')),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createStreamSchema(organizationId, input, "create-key"),
    ).resolves.toMatchObject({ schemaId: "camera-schema" });
    await expect(
      updateStreamSchemaDraft(
        organizationId,
        "camera-schema",
        "1",
        '"schema:1:1"',
        {
          display_name: "前视图像 v1",
          change_summary: "clarify name",
        },
        "update-key",
      ),
    ).resolves.toMatchObject({ etag: '"schema:1:2"' });
    const report = await validateStreamSchemaVersion(organizationId, {
      schemaId: "camera-schema",
      schemaVersion: "1",
      etag: '"schema:1:2"',
      idempotencyKey: "validate-key",
    });
    const preflight = await preflightDataSchemaPublish(organizationId, {
      schemaId: "camera-schema",
      schemaVersion: "1",
      etag: '"schema:1:2"',
      expectedHash: "a".repeat(64),
      validationReportId: report.id,
      compatibilityCheckId: report.compatibility_check_id,
      changeSummary: "publish validated version",
      idempotencyKey: "publish-key",
    });
    await expect(
      publishDataSchema(organizationId, {
        schemaId: "camera-schema",
        schemaVersion: "1",
        etag: '"schema:1:2"',
        idempotencyKey: "publish-key",
        preflightToken: preflight.preflight_token ?? "",
      }),
    ).resolves.toMatchObject({ status: "PUBLISHED" });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/organizations/org-schemas/stream-schemas",
      "/api/v1/organizations/org-schemas/stream-schemas/camera-schema/versions/1",
      "/api/v1/organizations/org-schemas/stream-schemas/camera-schema/versions/1:validate",
      "/api/v1/organizations/org-schemas/stream-schemas/camera-schema/versions/1:preflight-publish",
      "/api/v1/organizations/org-schemas/stream-schemas/camera-schema/versions/1:publish",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(
        new Headers((init as RequestInit).headers).get("Authorization"),
      ).toBe("Bearer p17-session");
      expect(
        new Headers((init as RequestInit).headers).get("X-Project-ID"),
      ).toBe(projectId);
    }
    const [, preflightInit] = fetchMock.mock.calls[3] ?? [];
    expect(JSON.parse(String((preflightInit as RequestInit).body))).toEqual({
      expected_hash: "a".repeat(64),
      expected_etag: '"schema:1:2"',
      validation_report_id: "validation-1",
      compatibility_check_id: "compatibility-1",
      change_summary: "publish validated version",
      acknowledge_warning_codes: [],
    });
  });

  it("uses the generated project-region dataset-reference contract with ETag and idempotency", async () => {
    const reference = {
      schema_id: "camera-schema",
      schema_version: "1",
      dataset_id: "dataset_camerafront",
      dataset_version_id: "version_release001",
      associated_by: "schema-publisher",
      associated_at: "2026-08-21T08:00:00Z",
    };
    const scope = {
      organizationId,
      projectId,
      regionCode: "cn-shanghai-1",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          items: [reference],
          page_info: {
            has_next_page: false,
            has_previous_page: false,
            start_cursor: null,
            end_cursor: null,
          },
          scope: {
            organization_id: organizationId,
            project_id: projectId,
            region_code: scope.regionCode,
          },
          request_id: "p17-reference-list",
          contract_version: "2026-08-21",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            data: reference,
            scope: {
              organization_id: organizationId,
              project_id: projectId,
              region_code: scope.regionCode,
            },
            request_id: "p17-reference-create",
            contract_version: "2026-08-21",
          },
          201,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listDataSchemaDatasetReferences(scope, "camera-schema", "1"),
    ).resolves.toEqual([reference]);
    await expect(
      associateDataSchemaDatasetReference(scope, {
        schemaId: "camera-schema",
        schemaVersion: "1",
        etag: '"camera-schema:1:3"',
        input: {
          dataset_id: reference.dataset_id,
          dataset_version_id: reference.dataset_version_id,
        },
        idempotencyKey: "reference-key",
      }),
    ).resolves.toEqual(reference);

    const base =
      "/api/v1/organizations/org-schemas/projects/project-schemas/regions/cn-shanghai-1/stream-schemas/camera-schema/versions/1/dataset-references";
    const [listUrl, listInit] = fetchMock.mock.calls[0] ?? [];
    expect(listUrl).toBe(base);
    expect(listInit?.method).toBe("GET");
    const [createUrl, createInit] = fetchMock.mock.calls[1] ?? [];
    expect(createUrl).toBe(base);
    expect(createInit?.method).toBe("POST");
    expect(JSON.parse(String(createInit?.body))).toEqual({
      dataset_id: reference.dataset_id,
      dataset_version_id: reference.dataset_version_id,
    });
    const createHeaders = new Headers(createInit?.headers);
    expect(createHeaders.get("If-Match")).toBe('"camera-schema:1:3"');
    expect(createHeaders.get("Idempotency-Key")).toBe("reference-key");
    expect(createHeaders.get("X-Project-ID")).toBe(projectId);
  });
});
