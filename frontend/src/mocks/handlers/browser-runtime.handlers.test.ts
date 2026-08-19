import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { resetAccessRequestHandlerState } from "./access.handlers";
import { resetAnnotationHandlerState } from "./annotation.handlers";
import { resetCollectionTaskHandlerState } from "./collection-tasks.handlers";
import { resetIngestHandlerState } from "./ingest.handlers";
import { handlers } from "./index";

const server = setupServer(...handlers);
const api = "http://localhost/api/v1";
const scopedHeaders = {
  Authorization: "Bearer fixture-bearer",
  "X-Client-Version": "browser-mock-test",
  "X-Organization-Id": "org_fx_01",
  "X-Project-Id": "prj_fx_01",
  "X-Region-Code": "cn-shanghai",
};

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  resetAccessRequestHandlerState();
  resetAnnotationHandlerState();
  resetCollectionTaskHandlerState();
  resetIngestHandlerState();
});
afterAll(() => server.close());

describe("Browser Mock runtime API coverage", () => {
  it("serves every current list page without falling through to a real API", async () => {
    const paths = [
      "/projects/prj_fx_01/collection-tasks",
      "/projects/prj_fx_01/annotation-tasks",
      "/projects/prj_fx_01/regions/cn-shanghai/upload-sessions",
      "/projects/prj_fx_01/membership-requests",
      "/projects/prj_fx_01/capability-requests",
    ];
    const responses = await Promise.all(
      paths.map((path) => fetch(`${api}${path}`, { headers: scopedHeaders })),
    );

    for (const [index, response] of responses.entries()) {
      expect(response.status, paths[index]).toBe(200);
      expect(
        response.headers.get("X-HC-Mock-Unhandled"),
        paths[index],
      ).toBeNull();
    }
  });

  it("serves the current upload, annotation and collection-task detail chains", async () => {
    const paths = [
      "/projects/prj_fx_01/collection-tasks/collection-task-fx-01/progress",
      "/projects/prj_fx_01/regions/cn-shanghai/upload-sessions/upload-session-fx-01",
      "/projects/prj_fx_01/regions/cn-shanghai/upload-sessions/upload-session-fx-01/manifest",
      "/projects/prj_fx_01/regions/cn-shanghai/rollouts/rollout-fx-01/quality",
      "/annotation-tasks/annotation-task-0142",
      "/annotation-tasks/annotation-task-0142/draft",
      "/annotation-tasks/annotation-task-0142/history",
      "/projects/prj_fx_01/tag-schemas/robot-operation/versions/7",
    ];
    const responses = await Promise.all(
      paths.map((path) => fetch(`${api}${path}`, { headers: scopedHeaders })),
    );

    for (const [index, response] of responses.entries()) {
      expect(response.status, paths[index]).toBe(200);
      expect(
        response.headers.get("X-HC-Mock-Unhandled"),
        paths[index],
      ).toBeNull();
    }
  });

  it("fails closed inside Browser Mock when a future API route has no fixture", async () => {
    const response = await fetch(`${api}/future-unhandled-route`, {
      headers: scopedHeaders,
    });
    expect(response.status).toBe(501);
    expect(response.headers.get("X-HC-Mock-Unhandled")).toBe("true");
    await expect(response.json()).resolves.toMatchObject({
      code: "MOCK_HANDLER_MISSING",
    });
  });
});
