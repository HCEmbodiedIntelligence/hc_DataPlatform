import { describe, expect, it } from "vitest";
import { routes } from "./routing";

describe("ingest routes", () => {
  it("builds explicit upload mode routes", () => {
    expect(routes.uploadJobs.path).toBe("/ingest/uploads/new");
    expect(routes.uploadJobs.build()).toBe("/ingest/uploads/new");
    expect(routes.uploadRecords.path).toBe("/ingest/uploads/records");
    expect(routes.uploadRecords.build({ status: "FAILED" })).toBe(
      "/ingest/uploads/records?status=FAILED",
    );
  });

  it("keeps stable upload detail routes separate from upload modes", () => {
    expect(routes.uploads.path).toBe("/ingest/uploads/:uploadId");
    expect(routes.uploads.build({ uploadId: "upload-42" })).toBe(
      "/ingest/uploads/upload-42",
    );
  });
});
