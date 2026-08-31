import { z } from "zod";
import { createElement } from "react";

const DEVELOPMENT_LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

declare global {
  // Populated by /config.js in the production container.
  var __HC_RUNTIME_CONFIG__: Partial<Record<string, unknown>> | undefined;
}

function isLoopbackHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "http:" && DEVELOPMENT_LOOPBACK_HOSTS.has(url.hostname)
    );
  } catch {
    return false;
  }
}

const publicUrlSchema = z
  .string()
  .trim()
  .min(1)
  .refine(
    (value) =>
      value.startsWith("/") ||
      /^https:\/\//u.test(value) ||
      (import.meta.env.DEV && isLoopbackHttpUrl(value)),
    {
      message:
        "must be an absolute HTTPS URL, an application-root relative path, or a development loopback HTTP URL",
    },
  );

const semverSchema = z
  .string()
  .regex(/^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/u);
const sha256Schema = z.string().regex(/^sha256:[0-9a-f]{64}$/u);
const releaseDigestSchema = z.union([sha256Schema, z.literal("unreleased")]);
const gitCommitSchema = z.union([
  z.string().regex(/^[0-9a-f]{40}$/u),
  z.literal("unknown"),
]);

const environmentSchema = z
  .object({
    VITE_API_BASE_URL: publicUrlSchema,
    VITE_SSE_BASE_URL: publicUrlSchema,
    VITE_MOCK_MODE: z.enum(["off", "browser", "test"]),
    VITE_BUILD_VERSION: z.string().trim().min(1),
    VITE_RELEASE_ENV: z.enum(["local", "dev", "test", "staging", "production"]),
    VITE_PLATFORM_VERSION: semverSchema,
    VITE_GIT_COMMIT: gitCommitSchema,
    VITE_CHART_VERSION: semverSchema,
    VITE_RELEASE_MANIFEST_DIGEST: releaseDigestSchema,
    VITE_MIGRATION_MANIFEST_DIGEST: releaseDigestSchema,
    VITE_COMPONENT_IMAGE_DIGEST: releaseDigestSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (
      value.VITE_RELEASE_ENV !== "staging" &&
      value.VITE_RELEASE_ENV !== "production"
    )
      return;
    if (
      !/^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/u.test(
        value.VITE_BUILD_VERSION,
      )
    ) {
      context.addIssue({
        code: "custom",
        path: ["VITE_BUILD_VERSION"],
        message: "must be an immutable platform-v release identifier",
      });
    }
    if (value.VITE_GIT_COMMIT === "unknown") {
      context.addIssue({
        code: "custom",
        path: ["VITE_GIT_COMMIT"],
        message: "must be immutable",
      });
    }
    for (const field of [
      "VITE_RELEASE_MANIFEST_DIGEST",
      "VITE_MIGRATION_MANIFEST_DIGEST",
      "VITE_COMPONENT_IMAGE_DIGEST",
    ] as const) {
      const digest = value[field];
      if (digest === "unreleased" || digest === `sha256:${"0".repeat(64)}`) {
        context.addIssue({
          code: "custom",
          path: [field],
          message: "must be a nonzero immutable digest",
        });
      }
    }
  });

export interface AppEnvironment {
  apiBaseUrl: string;
  sseBaseUrl: string;
  mockMode: "off" | "browser" | "test";
  buildVersion: string;
  releaseEnv: "local" | "dev" | "test" | "staging" | "production";
  releaseIdentity: {
    format_version: "hc-platform-release-identity/v1";
    release_id: string;
    semantic_version: string;
    git_commit: string;
    chart_version: string;
    release_manifest_digest: string;
    migration_manifest_digest: string;
    component: "frontend";
    component_image_digest: string;
  };
}

export type EnvironmentResult =
  | { ok: true; value: AppEnvironment }
  | { ok: false; issues: readonly string[] };

export function readAppEnvironment(): EnvironmentResult {
  const runtime = globalThis.__HC_RUNTIME_CONFIG__ ?? {};
  return readEnvironment({
    VITE_API_BASE_URL:
      runtime.VITE_API_BASE_URL ?? import.meta.env.VITE_API_BASE_URL,
    VITE_SSE_BASE_URL:
      runtime.VITE_SSE_BASE_URL ?? import.meta.env.VITE_SSE_BASE_URL,
    VITE_MOCK_MODE: runtime.VITE_MOCK_MODE ?? import.meta.env.VITE_MOCK_MODE,
    VITE_BUILD_VERSION:
      runtime.VITE_BUILD_VERSION ?? import.meta.env.VITE_BUILD_VERSION,
    VITE_RELEASE_ENV:
      runtime.VITE_RELEASE_ENV ?? import.meta.env.VITE_RELEASE_ENV,
    VITE_PLATFORM_VERSION:
      runtime.VITE_PLATFORM_VERSION ?? import.meta.env.VITE_PLATFORM_VERSION,
    VITE_GIT_COMMIT: runtime.VITE_GIT_COMMIT ?? import.meta.env.VITE_GIT_COMMIT,
    VITE_CHART_VERSION:
      runtime.VITE_CHART_VERSION ?? import.meta.env.VITE_CHART_VERSION,
    VITE_RELEASE_MANIFEST_DIGEST:
      runtime.VITE_RELEASE_MANIFEST_DIGEST ??
      import.meta.env.VITE_RELEASE_MANIFEST_DIGEST,
    VITE_MIGRATION_MANIFEST_DIGEST:
      runtime.VITE_MIGRATION_MANIFEST_DIGEST ??
      import.meta.env.VITE_MIGRATION_MANIFEST_DIGEST,
    VITE_COMPONENT_IMAGE_DIGEST:
      runtime.VITE_COMPONENT_IMAGE_DIGEST ??
      import.meta.env.VITE_COMPONENT_IMAGE_DIGEST,
  });
}

export function readEnvironment(source: unknown): EnvironmentResult {
  const values =
    typeof source === "object" && source !== null
      ? (source as Record<string, unknown>)
      : {};
  const result = environmentSchema.safeParse({
    VITE_API_BASE_URL: values.VITE_API_BASE_URL,
    VITE_SSE_BASE_URL: values.VITE_SSE_BASE_URL,
    VITE_MOCK_MODE: values.VITE_MOCK_MODE,
    VITE_BUILD_VERSION: values.VITE_BUILD_VERSION,
    VITE_RELEASE_ENV: values.VITE_RELEASE_ENV,
    VITE_PLATFORM_VERSION: values.VITE_PLATFORM_VERSION,
    VITE_GIT_COMMIT: values.VITE_GIT_COMMIT,
    VITE_CHART_VERSION: values.VITE_CHART_VERSION,
    VITE_RELEASE_MANIFEST_DIGEST: values.VITE_RELEASE_MANIFEST_DIGEST,
    VITE_MIGRATION_MANIFEST_DIGEST: values.VITE_MIGRATION_MANIFEST_DIGEST,
    VITE_COMPONENT_IMAGE_DIGEST: values.VITE_COMPONENT_IMAGE_DIGEST,
  });
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map(
        (issue) => `${issue.path.join(".")}: ${issue.message}`,
      ),
    };
  }
  return {
    ok: true,
    value: {
      apiBaseUrl: result.data.VITE_API_BASE_URL,
      sseBaseUrl: result.data.VITE_SSE_BASE_URL,
      mockMode: result.data.VITE_MOCK_MODE,
      buildVersion: result.data.VITE_BUILD_VERSION,
      releaseEnv: result.data.VITE_RELEASE_ENV,
      releaseIdentity: {
        format_version: "hc-platform-release-identity/v1",
        release_id: result.data.VITE_BUILD_VERSION,
        semantic_version: result.data.VITE_PLATFORM_VERSION,
        git_commit: result.data.VITE_GIT_COMMIT,
        chart_version: result.data.VITE_CHART_VERSION,
        release_manifest_digest: result.data.VITE_RELEASE_MANIFEST_DIGEST,
        migration_manifest_digest: result.data.VITE_MIGRATION_MANIFEST_DIGEST,
        component: "frontend",
        component_image_digest: result.data.VITE_COMPONENT_IMAGE_DIGEST,
      },
    },
  };
}

export function StartupErrorPage({ issues }: { issues: readonly string[] }) {
  return createElement(
    "main",
    {
      className: "startup-error",
      role: "alert",
      "aria-labelledby": "startup-error-title",
    },
    createElement("h1", { id: "startup-error-title" }, "应用无法安全启动"),
    createElement(
      "p",
      null,
      "公开环境配置缺失或无效。应用没有发起任何接口请求。",
    ),
    createElement(
      "ul",
      null,
      issues.map((issue) => createElement("li", { key: issue }, issue)),
    ),
  );
}
