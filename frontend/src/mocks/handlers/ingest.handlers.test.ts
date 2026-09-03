import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { ingestHandlers, resetIngestHandlerState } from "./ingest.handlers";
import { resetIngestScenario, setIngestScenario } from "../scenarios/ingest";

interface UploadListResponse {
  readonly items: readonly {
    readonly session_id?: string;
    readonly status: string;
  }[];
}

const server = setupServer(...ingestHandlers);
const endpoint =
  "http://localhost/api/v1/projects/prj_fx_01/regions/cn-shanghai/upload-sessions";

async function listUploads(query: string): Promise<UploadListResponse> {
  const response = await fetch(`${endpoint}?${query}`, {
    headers: { "X-Client-Version": "test" },
  });
  expect(response.ok).toBe(true);
  return response.json() as Promise<UploadListResponse>;
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  resetIngestHandlerState();
  resetIngestScenario();
});
afterAll(() => server.close());

describe("upload session list filters", () => {
  it("filters by the current upload status", async () => {
    const committed = await listUploads("status=RAW_COMMITTED");
    expect(committed.items.map((item) => item.session_id)).toEqual([
      "upload-session-fx-01",
    ]);

    const uploading = await listUploads("status=UPLOADING");
    expect(uploading.items).toEqual([]);
  });

  it("applies the current status to the job-failed scenario", async () => {
    setIngestScenario("job-failed");

    const failed = await listUploads("status=FAILED");
    expect(failed.items.map((item) => item.session_id)).toEqual([
      "upload-session-fx-01",
    ]);

    const committed = await listUploads("status=RAW_COMMITTED");
    expect(committed.items).toEqual([]);
  });

  it("rejects removed query fields", async () => {
    const response = await fetch(`${endpoint}?lifecycle_status=UPLOADING`, {
      headers: { "X-Client-Version": "test" },
    });
    expect(response.status).toBe(422);
  });
});
