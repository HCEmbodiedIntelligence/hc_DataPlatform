import { describe, expect, it } from "vitest";
import { routes } from "./routing";

describe("problem-data Raw diagnostic routing", () => {
  it("keeps the diagnostic inside the problem-data route and preserves filters", () => {
    expect(
      routes.manualIssueRawDiagnostic.build({
        uploadId: "session-a",
        returnTo: "/manual/issues?source=AUTO_QC&severity=CRITICAL",
      }),
    ).toBe(
      "/manual/issues/raw-diagnostic/session-a?returnTo=%2Fmanual%2Fissues%3Fsource%3DAUTO_QC%26severity%3DCRITICAL",
    );
  });

  it("does not accept an unrelated page as the problem-data return target", () => {
    expect(
      routes.manualIssueRawDiagnostic.build({
        uploadId: "session-a",
        returnTo: "/ingest/uploads/records",
      }),
    ).toBe("/manual/issues/raw-diagnostic/session-a");
  });
});
