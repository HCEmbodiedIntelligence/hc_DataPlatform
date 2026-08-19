import type { APIRequestContext } from "@playwright/test";
import { createHmac, randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const repositoryRoot = resolve(here, "../../..");

export interface RealRunScope {
  readonly runId: string;
  readonly projectId: string;
  readonly foreignProjectId: string;
  readonly regionCode: string;
  readonly adminUsername: string;
  readonly contractorUsername: string;
}

export interface SafeGatewayResponse {
  readonly status: number;
  readonly body: unknown;
  readonly requestId: string | null;
  readonly etag: string | null;
}

export interface CleanupResult {
  readonly database: Readonly<Record<string, number>>;
  readonly objects: Readonly<Record<string, number>>;
}

export interface MainChainArtifact {
  readonly schemaVersion: 1;
  readonly runId: string;
  readonly projectId: string;
  readonly foreignProjectId: string;
  readonly regionCode: string;
  readonly status: "RUNNING" | "PASSED" | "FAILED";
  readonly startedAt: string;
  readonly finishedAt: string | null;
  readonly stages: readonly {
    readonly name: string;
    readonly status: "PASSED" | "FAILED" | "NOT_RUN";
    readonly requestIds: readonly string[];
    readonly resourceIds: Readonly<Record<string, string>>;
  }[];
  readonly network: {
    readonly sameOriginApiResponseCount: number;
    readonly responseStatusCounts: Readonly<Record<string, number>>;
    readonly objectStoreRequests: readonly {
      readonly origin: string;
      readonly host: string;
      readonly method: string;
      readonly status: number;
      readonly elapsedMs: number;
    }[];
    readonly mockWorkerRegistrations: number;
    readonly mockUrlsObserved: number;
    readonly trace: "OFF_CREDENTIAL_SAFETY";
    readonly video: "OFF_CREDENTIAL_SAFETY";
  };
  readonly cleanup: {
    readonly before: CleanupResult | null;
    readonly after: CleanupResult | null;
    readonly verification: CleanupResult | null;
  };
}

function runOwner(): "fe11" | "fe12" | "fe14" | "fe16" {
  const owner = process.env.HC_REAL_API_E2E_RUN_OWNER ?? "fe11";
  if (
    owner !== "fe11" &&
    owner !== "fe12" &&
    owner !== "fe14" &&
    owner !== "fe16"
  ) {
    throw new Error(
      "HC_REAL_API_E2E_RUN_OWNER must be fe11, fe12, fe14 or fe16",
    );
  }
  return owner;
}

export function createRunScope(): RealRunScope {
  const runId = `${runOwner()}-${Date.now().toString(36)}-${randomBytes(3).toString("hex")}`;
  return {
    runId,
    projectId: `be22-${runId}-p1`,
    foreignProjectId: `be22-${runId}-p2`,
    regionCode: `be22-${runId}-cn`,
    adminUsername: `be22-${runId}-admin`,
    contractorUsername: `be22-${runId}-contractor`,
  };
}

function base64Url(value: string | Buffer): string {
  return Buffer.from(value).toString("base64url");
}

export function issueBootstrapAdminToken(
  subject: string,
  scope: RealRunScope,
): string {
  const supplied = process.env.HC_REAL_API_BOOTSTRAP_ADMIN_TOKEN;
  if (supplied) return supplied;
  if (process.env.HC_ENVIRONMENT !== "test") {
    throw new Error(
      "Real browser bootstrap JWT issuance requires HC_ENVIRONMENT=test",
    );
  }
  const signingKey = process.env.HC_WAVE2_JWT_SIGNING_KEY ?? "";
  if (signingKey.length < 32) {
    throw new Error(
      "HC_WAVE2_JWT_SIGNING_KEY must contain at least 32 characters",
    );
  }
  const now = Math.floor(Date.now() / 1_000);
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64Url(
    JSON.stringify({
      iss: process.env.HC_JWT_ISSUER ?? "https://be22.test.invalid/",
      aud: process.env.HC_JWT_AUDIENCE ?? "hc-data-platform",
      sub: subject,
      iat: now,
      exp: now + 30 * 60,
      roles: [],
      project_ids: [scope.projectId],
      region_codes: [scope.regionCode],
      capabilities: ["project.access.manage"],
      capability_revision: 0,
      service_identity: false,
    }),
  );
  const unsigned = `${header}.${payload}`;
  const signature = createHmac("sha256", signingKey)
    .update(unsigned)
    .digest("base64url");
  return `${unsigned}.${signature}`;
}

export async function gatewayRequest(
  request: APIRequestContext,
  input: {
    readonly method: string;
    readonly path: string;
    readonly token?: string;
    readonly headers?: Readonly<Record<string, string>>;
    readonly body?: unknown;
  },
): Promise<SafeGatewayResponse> {
  const headers: Record<string, string> = {
    Accept: "application/json, application/problem+json",
    ...input.headers,
  };
  if (input.token) headers.Authorization = `Bearer ${input.token}`;
  const response = await request.fetch(`/api/v1${input.path}`, {
    method: input.method,
    headers,
    ...(input.body === undefined ? {} : { data: input.body }),
  });
  const responseText = await response.text();
  let body: unknown;
  try {
    body = responseText ? (JSON.parse(responseText) as unknown) : null;
  } catch {
    body = null;
  }
  return {
    status: response.status(),
    body,
    requestId: response.headers()["x-request-id"] ?? null,
    etag: response.headers().etag ?? null,
  };
}

export function objectBody(
  value: unknown,
  label: string,
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} did not return a JSON object`);
  }
  return value as Record<string, unknown>;
}

export function stringField(
  value: Record<string, unknown>,
  key: string,
  label: string,
): string {
  const field = value[key];
  if (typeof field !== "string" || !field) {
    throw new Error(`${label} did not return ${key}`);
  }
  return field;
}

export function renderLegalManifest(
  scope: RealRunScope,
  collectionTaskId: string,
): Record<string, unknown> {
  const template = JSON.parse(
    readFileSync(
      resolve(
        repositoryRoot,
        "backend/tests/system/wave2/data/manifests/legal.json",
      ),
      "utf8",
    ),
  ) as Record<string, unknown>;
  return {
    ...template,
    project_id: scope.projectId,
    task_id: collectionTaskId,
    collection_job_id: `${scope.runId}-legal-job`,
    rollout_id: `${scope.runId}-legal-rollout`,
    collection_session_id: `${scope.runId}-legal-session`,
    recording_request_id: `${scope.runId}-legal-request`,
    data_package_id: `${scope.runId}-legal-package`,
    sequence_no: 1,
  };
}

const cleanupScript = String.raw`
import json
import os
import sys
import boto3
from botocore.config import Config
from tests.system.wave2.cleanup import PostgresS3CleanupBackend
from tests.system.wave2.fixture import RunScope

scope = RunScope.create(sys.argv[1])
client = boto3.client(
    "s3",
    endpoint_url=os.environ["HC_MINIO_ENDPOINT"],
    aws_access_key_id=os.environ["HC_MINIO_ACCESS_KEY"],
    aws_secret_access_key=os.environ["HC_MINIO_SECRET_KEY"],
    region_name=os.environ.get("HC_OBJECT_STORE_REGION", "us-east-1"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
backend = PostgresS3CleanupBackend(
    os.environ["HC_TEST_POSTGRES_DSN"],
    client,
    os.environ["HC_MINIO_BUCKET"],
)
database = backend.delete_database_scope(scope)
objects = {prefix: backend.delete_object_prefix(prefix) for prefix in scope.object_prefixes}
print(json.dumps({"database": database, "objects": objects}, sort_keys=True))
`;

export function requireRealEnvironment(): void {
  const required = [
    "HC_TEST_POSTGRES_DSN",
    "HC_MINIO_ENDPOINT",
    "HC_MINIO_ACCESS_KEY",
    "HC_MINIO_SECRET_KEY",
    "HC_MINIO_BUCKET",
    "HC_WAVE2_TEST_DATABASE_ACK",
  ] as const;
  const missing: string[] = required.filter((key) => !process.env[key]);
  if (process.env.HC_ENVIRONMENT !== "test")
    missing.push("HC_ENVIRONMENT=test");
  if (process.env.HC_WAVE2_CLEANUP_ENABLED !== "1") {
    missing.push("HC_WAVE2_CLEANUP_ENABLED=1");
  }
  if (
    !process.env.HC_REAL_API_BOOTSTRAP_ADMIN_TOKEN &&
    (process.env.HC_WAVE2_JWT_SIGNING_KEY?.length ?? 0) < 32
  ) {
    missing.push("HC_WAVE2_JWT_SIGNING_KEY(>=32)");
  }
  if (missing.length > 0) {
    throw new Error(
      `Real API environment is incomplete: ${missing.join(", ")}`,
    );
  }
}

export function cleanupRun(scope: RealRunScope): CleanupResult {
  const output = execFileSync(
    resolve(repositoryRoot, "backend/.venv/bin/python"),
    ["-c", cleanupScript, scope.runId],
    {
      cwd: resolve(repositoryRoot, "backend"),
      encoding: "utf8",
      env: {
        ...process.env,
        PYTHONPATH: [
          resolve(repositoryRoot, "backend/src"),
          resolve(repositoryRoot, "backend"),
          process.env.PYTHONPATH,
        ]
          .filter(Boolean)
          .join(":"),
      },
      maxBuffer: 2 * 1024 * 1024,
    },
  );
  return JSON.parse(output) as CleanupResult;
}

export function totalCleanupCount(result: CleanupResult): number {
  return [
    ...Object.values(result.database),
    ...Object.values(result.objects),
  ].reduce((sum, count) => sum + count, 0);
}

const secretPattern =
  /authorization|cookie|password|secret|signed_url|presigned_url|token|bearer\s+|x-amz-(?:signature|credential)/iu;

export function writeSecretFreeArtifact(artifact: MainChainArtifact): string {
  const serialized = `${JSON.stringify(artifact, null, 2)}\n`;
  if (secretPattern.test(serialized)) {
    throw new Error("Refusing to write an artifact containing a secret marker");
  }
  const target = resolve(
    repositoryRoot,
    "artifacts/visual/e01-e10",
    {
      fe11: "FE11-repair",
      fe12: "FE12-final",
      fe14: "FE14-final",
      fe16: "FE16-final",
    }[runOwner()],
    `real-api-main-chain-${artifact.runId}.json`,
  );
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, serialized, { encoding: "utf8", mode: 0o600 });
  return target;
}
