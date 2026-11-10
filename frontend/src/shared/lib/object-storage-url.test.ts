// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { localObjectStorageUrl } from "./object-storage-url";
const origin = "https://platform.example.test:8443";
const path =
  "/hc-data-local/episode-deliveries/a%20b/export.zip?response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27%25E6%25B5%258B.zip&X-Amz-Signature=abcdef&tag=x%2By";
beforeEach(() => {
  vi.stubEnv("DEV", true);
  vi.stubEnv("VITE_ROBOT_PLATFORM_BASE_URL", origin);
  vi.stubGlobal("window", {
    location: new URL("http://127.0.0.1:8088/exports"),
  });
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it.each(["127.0.0.1", "localhost", "[::1]"])(
  "routes local reads on %s without changing the signing host or encoded query",
  (host) => {
    vi.stubGlobal("window", {
      location: new URL(`http://${host}:8088/exports`),
    });
    expect(localObjectStorageUrl(origin + path)).toBe(
      "/_recording_media/platform.example.test:8443" + path,
    );
  },
);
it.each([
  "https://unrelated.example.test" + path,
  origin + "/other-bucket/export.zip?X-Amz-Signature=abcdef",
  origin + "/hc-data-local/export.zip",
  "http://127.0.0.1:8088" + path,
  "https://user:password@platform.example.test:8443" + path,
])("leaves unsupported URLs untouched: %s", (url) => {
  expect(localObjectStorageUrl(url)).toBe(url);
});
it("keeps public and production downloads on their authorized origin", () => {
  vi.stubGlobal("window", { location: new URL(origin + "/exports") });
  expect(localObjectStorageUrl(origin + path)).toBe(origin + path);
  vi.stubGlobal("window", {
    location: new URL("http://localhost:8088/exports"),
  });
  vi.stubEnv("DEV", false);
  expect(localObjectStorageUrl(origin + path)).toBe(origin + path);
});
it("does not require a browser when processing a grant outside the UI", () => {
  vi.stubGlobal("window", undefined);
  expect(localObjectStorageUrl(origin + path)).toBe(origin + path);
});
