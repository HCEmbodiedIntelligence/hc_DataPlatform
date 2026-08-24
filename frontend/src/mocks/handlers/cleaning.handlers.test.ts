import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { fixtureScope } from "../fixtures/common/scope";
import { cleaningFixtureScope } from "../fixtures/cleaning";
import { setCleaningScenario } from "../scenarios/cleaning";
import cleaningHandlers from "./cleaning.handlers";

const server = setupServer(...cleaningHandlers);
const baseUrl = `http://localhost/api/v1/projects/${fixtureScope.projectId}/regions/${fixtureScope.regionCode}`;
const headers = { "X-Client-Version": "test" };

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  server.resetHandlers();
  setCleaningScenario("happy");
});

afterAll(() => {
  server.close();
});

describe("cleaning mock scope", () => {
  it("uses the default application scope", () => {
    expect(cleaningFixtureScope).toEqual({
      organization_id: fixtureScope.organizationId,
      project_id: fixtureScope.projectId,
      region_code: fixtureScope.regionCode,
    });
  });

  it.each([
    "/manual-issues",
    "/manual-issues:page",
    "/cleaning-drafts",
    "/cleaning-drafts:summary",
  ])(
    "serves %s from a bare local URL",
    async (path) => {
      const response = await fetch(`${baseUrl}${path}`, { headers });
      expect(response.status).toBe(200);
    },
  );

  it("creates a source-bound ManualIssue without an If-Match header", async () => {
    const response = await fetch(`${baseUrl}/manual-issues`, {
      method: "POST",
      headers: {
        ...headers,
        "Content-Type": "application/json",
        "Idempotency-Key": "p09-mock-create",
      },
      body: JSON.stringify({
        origin_dataset_version_id: "version_fx_mc_base_01",
        episode_id: "episode_fx_mc_01",
        episode_revision_id: "revision_fx_mc_base_01",
        episode_stream_id: "stream_fx_mc_camera_01",
        start_ns: "100",
        end_ns: "200",
        issue_type: "POSE_JITTER",
        severity: "HIGH",
        note: "Source-bound mock ManualIssue.",
      }),
    });

    expect(response.status).toBe(201);
    await expect(response.json()).resolves.toMatchObject({
      data: {
        id: "issue_fx_mc_created_01",
        status: "OPEN",
        allowed_actions: ["VIEW_EPISODE", "TRIAGE", "CREATE_DRAFT"],
      },
    });
  });
});
