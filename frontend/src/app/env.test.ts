import { afterEach, describe, expect, it } from "vitest";
import { readEnvironment } from "./env";
import {
  configureReleaseIdentity,
  getReleaseIdentity,
  resetReleaseIdentityForTests,
} from "../shared/config/release-identity";

const productionEnvironment = {
  VITE_API_BASE_URL: "/api/v1",
  VITE_SSE_BASE_URL: "/api/v1/events",
  VITE_MOCK_MODE: "off",
  VITE_BUILD_VERSION: "platform-v0.1.0-test.1",
  VITE_RELEASE_ENV: "production",
  VITE_PLATFORM_VERSION: "0.1.0",
  VITE_GIT_COMMIT: "1".repeat(40),
  VITE_CHART_VERSION: "0.1.0",
  VITE_RELEASE_MANIFEST_DIGEST: `sha256:${"a".repeat(64)}`,
  VITE_MIGRATION_MANIFEST_DIGEST: `sha256:${"b".repeat(64)}`,
  VITE_COMPONENT_IMAGE_DIGEST: `sha256:${"c".repeat(64)}`,
} as const;

afterEach(() => resetReleaseIdentityForTests());

describe("frontend release identity", () => {
  it("materializes the exact immutable production identity once", () => {
    const result = readEnvironment(productionEnvironment);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.issues.join(", "));

    configureReleaseIdentity(result.value.releaseIdentity);
    expect(getReleaseIdentity()).toEqual({
      format_version: "hc-platform-release-identity/v1",
      release_id: productionEnvironment.VITE_BUILD_VERSION,
      semantic_version: "0.1.0",
      git_commit: productionEnvironment.VITE_GIT_COMMIT,
      chart_version: "0.1.0",
      release_manifest_digest:
        productionEnvironment.VITE_RELEASE_MANIFEST_DIGEST,
      migration_manifest_digest:
        productionEnvironment.VITE_MIGRATION_MANIFEST_DIGEST,
      component: "frontend",
      component_image_digest: productionEnvironment.VITE_COMPONENT_IMAGE_DIGEST,
    });
    expect(Object.isFrozen(getReleaseIdentity())).toBe(true);
    expect(() =>
      configureReleaseIdentity(result.value.releaseIdentity),
    ).toThrow("already initialized");
  });

  it.each([
    ["VITE_BUILD_VERSION", "unreleased"],
    ["VITE_GIT_COMMIT", "unknown"],
    ["VITE_RELEASE_MANIFEST_DIGEST", "unreleased"],
    ["VITE_MIGRATION_MANIFEST_DIGEST", `sha256:${"0".repeat(64)}`],
    ["VITE_COMPONENT_IMAGE_DIGEST", "unreleased"],
  ] as const)("rejects non-immutable production %s", (field, value) => {
    const result = readEnvironment({
      ...productionEnvironment,
      [field]: value,
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected invalid release identity");
    expect(result.issues.some((issue) => issue.startsWith(field))).toBe(true);
  });

  it("allows explicit development sentinels without claiming a release", () => {
    const result = readEnvironment({
      ...productionEnvironment,
      VITE_BUILD_VERSION: "web-local",
      VITE_RELEASE_ENV: "local",
      VITE_GIT_COMMIT: "unknown",
      VITE_RELEASE_MANIFEST_DIGEST: "unreleased",
      VITE_MIGRATION_MANIFEST_DIGEST: "unreleased",
      VITE_COMPONENT_IMAGE_DIGEST: "unreleased",
    });
    expect(result.ok).toBe(true);
  });
});
