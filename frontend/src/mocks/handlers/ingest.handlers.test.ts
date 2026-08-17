import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { ingestHandlers, resetIngestHandlerState } from "./ingest.handlers";
import { resetIngestScenario, setIngestScenario } from "../scenarios/ingest";

interface UploadListResponse {
  readonly items: readonly {
    readonly upload_id: string;
    readonly lifecycle_status: string;
    readonly verification_status: string;
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
  it("applies the lifecycle statuses used by the uploading tab", async () => {
    const uploading = await listUploads(
      "lifecycle_status=CREATED&lifecycle_status=AUTHORIZING&lifecycle_status=UPLOADING&lifecycle_status=PAUSED&lifecycle_status=FINALIZING",
    );
    expect(uploading.items.map((item) => item.upload_id)).toEqual([
      "upload_fx_uploading",
    ]);

    const completed = await listUploads("lifecycle_status=AVAILABLE");
    expect(completed.items).toEqual([]);
  });

  it("applies lifecycle statuses to the job-failed scenario", async () => {
    setIngestScenario("job-failed");

    const failed = await listUploads(
      "lifecycle_status=FAILED&lifecycle_status=QUARANTINED&lifecycle_status=EXPIRED",
    );
    expect(failed.items.map((item) => item.upload_id)).toEqual([
      "upload_fx_quarantined",
    ]);

    const uploading = await listUploads("lifecycle_status=UPLOADING");
    expect(uploading.items).toEqual([]);
  });
});
