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
});
