import { describe, expect, it } from "vitest";
import { exportDownloadName } from "./download";

describe("export artifact names", () => {
  it("uses the MIME type rather than assuming every format is ZIP", () => {
    expect(exportDownloadName("data", "application/zip")).toBe("data.zip");
    expect(exportDownloadName("data", "application/json; charset=utf-8")).toBe(
      "data.json",
    );
    expect(
      exportDownloadName("new-format", "application/vnd.future-format"),
    ).toBe("new-format");
  });
  it("keeps titles from becoming filesystem paths", () => {
    expect(exportDownloadName("project/export", "application/json")).toBe(
      "project_export.json",
    );
  });
});
