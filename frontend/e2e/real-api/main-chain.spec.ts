import { expect, test } from "@playwright/test";
import type {
  APIRequestContext,
  BrowserContext,
  Page,
  Response,
} from "@playwright/test";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  cleanupRun,
  createRunScope,
  gatewayRequest,
  issueBootstrapAdminToken,
  objectBody,
  renderLegalManifest,
  repositoryRoot,
  requireRealEnvironment,
  stringField,
  totalCleanupCount,
  writeSecretFreeArtifact,
  type CleanupResult,
  type MainChainArtifact,
  type RealRunScope,
  type SafeGatewayResponse,
} from "./real-api-harness";

const enabled = process.env.HC_REAL_API_E2E_ENABLED === "1";
test.skip(
  !enabled,
  "NOT RUN: set HC_REAL_API_E2E_ENABLED=1 with the isolated gateway, Worker, PostgreSQL and MinIO test profile",
);

type Stage = MainChainArtifact["stages"][number];

class UploadSmokeComplete extends Error {}

interface BrowserIdentity {
  readonly context: BrowserContext;
  readonly page: Page;
  readonly principalId: string;
  readonly token: string;
  readonly password: string;
  readonly requestIds: readonly string[];
}

function responseRequestId(response: Response): string | null {
  return response.headers()["x-request-id"] ?? null;
}

function compactIds(values: readonly (string | null | undefined)[]): string[] {
  return [
    ...new Set(values.filter((value): value is string => Boolean(value))),
  ];
}

function apiResponse(
  response: Response,
  method: string,
  path: RegExp,
): boolean {
  const url = new URL(response.url());
  return response.request().method() === method && path.test(url.pathname);
}

function securePassword(): string {
  return `Aa1!${randomBytes(18).toString("base64url")}`;
}

async function assertNoMockWorker(page: Page): Promise<number> {
  return page.evaluate(async () => {
    if (!("serviceWorker" in navigator)) return 0;
    return (await navigator.serviceWorker.getRegistrations()).filter((item) =>
      /mockServiceWorker|\bmsw\b/iu.test(item.active?.scriptURL ?? ""),
    ).length;
  });
}

async function registerAndLogin(
  context: BrowserContext,
  username: string,
): Promise<BrowserIdentity> {
  const page = await context.newPage();
  const password = securePassword();
  await page.goto("/auth/register", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "创建账户" })).toBeVisible();
  expect(await assertNoMockWorker(page)).toBe(0);

  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("确认密码", { exact: true }).fill(password);
  const registrationPromise = page.waitForResponse((response) =>
    apiResponse(response, "POST", /\/api\/v1\/auth\/registrations$/u),
  );
  await page.getByRole("button", { name: "创建账户" }).click();
  const registration = await registrationPromise;
  expect(registration.status()).toBe(201);
  const registrationBody = objectBody(
    await registration.json(),
    "registration",
  );
  const principal = objectBody(
    registrationBody.principal,
    "registration principal",
  );
  const principalId = stringField(
    principal,
    "principal_id",
    "registration principal",
  );

  await expect(page).toHaveURL(/\/auth\/registered(?:\?|$)/u);
  await page.getByLabel("密码", { exact: true }).fill(password);
  const sessionPromise = page.waitForResponse((response) =>
    apiResponse(response, "POST", /\/api\/v1\/auth\/sessions$/u),
  );
  const bootstrapPromise = page.waitForResponse((response) =>
    apiResponse(response, "GET", /\/api\/v1\/auth\/session\/bootstrap$/u),
  );
  await page.getByRole("button", { name: /登\s*录/u }).click();
  const session = await sessionPromise;
  expect(session.status()).toBe(201);
  const sessionBody = objectBody(await session.json(), "session");
  const token = stringField(sessionBody, "access_token", "session");
  const bootstrap = await bootstrapPromise;
  expect(bootstrap.status()).toBe(200);
  const bootstrapBody = objectBody(await bootstrap.json(), "bootstrap");
  expect(bootstrapBody.available_scopes).toEqual([]);
  await expect(page).toHaveURL(/\/account\/empty(?:\?|$)/u);

  return {
    context,
    page,
    principalId,
    token,
    password,
    requestIds: compactIds([
      responseRequestId(registration),
      responseRequestId(session),
      responseRequestId(bootstrap),
    ]),
  };
}

async function loginExisting(
  page: Page,
  username: string,
  password: string,
): Promise<{ readonly token: string; readonly requestIds: readonly string[] }> {
  await page.goto("/auth/login", { waitUntil: "domcontentloaded" });
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  const sessionPromise = page.waitForResponse((response) =>
    apiResponse(response, "POST", /\/api\/v1\/auth\/sessions$/u),
  );
  const bootstrapPromise = page.waitForResponse((response) =>
    apiResponse(response, "GET", /\/api\/v1\/auth\/session\/bootstrap$/u),
  );
  await page.getByRole("button", { name: /登\s*录/u }).click();
  const session = await sessionPromise;
  expect(session.status()).toBe(201);
  const token = stringField(
    objectBody(await session.json(), "renewed session"),
    "access_token",
    "renewed session",
  );
  const bootstrap = await bootstrapPromise;
  expect(bootstrap.status()).toBe(200);
  await expect(page).not.toHaveURL(/\/auth\/login/u);
  return {
    token,
    requestIds: compactIds([
      responseRequestId(session),
      responseRequestId(bootstrap),
    ]),
  };
}

async function navigateInApp(page: Page, path: string): Promise<void> {
  await page.evaluate((nextPath) => {
    window.history.pushState(null, "", nextPath);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
  await expect(page).toHaveURL(
    new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}$`, "u"),
  );
}

async function submitMembership(
  page: Page,
  projectId: string,
): Promise<Response> {
  await page.getByRole("button", { name: "申请加入项目" }).click();
  await page.getByLabel("项目 ID").fill(projectId);
  await page
    .getByLabel("申请说明（选填）")
    .fill("FE11 isolated browser main chain");
  const responsePromise = page.waitForResponse((response) =>
    apiResponse(response, "POST", /\/membership-requests$/u),
  );
  await page.getByRole("button", { name: "提交加入申请" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(201);
  await expect(page.getByText("项目加入申请已提交")).toBeVisible();
  return response;
}

async function submitCapabilities(
  page: Page,
  projectId: string,
  capabilities: readonly string[],
): Promise<Response> {
  await expect(
    page.getByRole("heading", { name: "申请项目权限" }),
  ).toBeVisible();
  await page.getByLabel("项目 ID").fill(projectId);
  await page
    .getByLabel("Capability（逗号或换行分隔）")
    .fill(capabilities.join(", "));
  await page
    .getByLabel("申请说明（选填）")
    .fill("Minimum capabilities for the isolated FE11 chain");
  const responsePromise = page.waitForResponse((response) =>
    apiResponse(response, "POST", /\/capability-requests$/u),
  );
  await page.getByRole("button", { name: "提交权限申请" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(201);
  await expect(page.getByText("权限申请已提交")).toBeVisible();
  return response;
}

async function refreshEmptyAccount(
  page: Page,
  expected: "capability" | "shell",
) {
  const bootstrapPromise = page.waitForResponse((response) =>
    apiResponse(response, "GET", /\/api\/v1\/auth\/session\/bootstrap$/u),
  );
  await page.getByRole("button", { name: "审批后刷新" }).click();
  const bootstrap = await bootstrapPromise;
  expect(bootstrap.status()).toBe(200);
  if (expected === "capability") {
    await expect(page).toHaveURL(/\/account\/empty\?intent=capability$/u);
  } else {
    await expect(page).not.toHaveURL(/\/account\/empty/u);
    await expect(page.getByLabel("当前项目")).toBeVisible();
  }
  return bootstrap;
}

async function selectRegion(page: Page, regionCode: string): Promise<void> {
  const selector = page.getByLabel("当前区域");
  await expect(selector).toBeVisible();
  await selector.fill(regionCode);
  await selector.press("Enter");
  await expect(selector.locator("xpath=ancestor::label[1]")).toContainText(
    regionCode,
  );
}

async function approveWithBootstrapToken(
  request: APIRequestContext,
  token: string,
  projectId: string,
  kind: "membership" | "capability",
  requestId: string,
  key: string,
): Promise<SafeGatewayResponse> {
  const response = await gatewayRequest(request, {
    method: "POST",
    path: `/projects/${encodeURIComponent(projectId)}/${kind}-requests/${encodeURIComponent(requestId)}:approve`,
    token,
    headers: { "Idempotency-Key": key },
    body: {
      reason: "Isolated test bootstrap for the first project administrator",
    },
  });
  expect(response.status).toBe(200);
  expect(objectBody(response.body, `${kind} approval`).status).toBe("APPROVED");
  return response;
}

async function approveInUi(
  page: Page,
  projectId: string,
  kind: "membership" | "capability",
  requestId: string,
  decision: "approve" | "reject" = "approve",
): Promise<Response> {
  const tab =
    kind === "membership" ? "membership-requests" : "capability-requests";
  await navigateInApp(
    page,
    `/settings/access?tab=${tab}&status=PENDING&requestId=${encodeURIComponent(requestId)}`,
  );
  const refreshPromise = page.waitForResponse((response) =>
    apiResponse(response, "GET", new RegExp(`/${tab}(?:\\?.*)?$`, "u")),
  );
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  const refreshed = await refreshPromise;
  expect(refreshed.status()).toBe(200);
  const drawer = page.getByRole("dialog", { name: "审批申请" });
  await expect(drawer).toBeVisible();
  await expect(
    drawer.getByText(projectId, { exact: true }).first(),
  ).toBeVisible();
  const decisionLabel = decision === "approve" ? "批准申请" : "拒绝申请";
  const submitLabel = decision === "approve" ? "提交批准申请" : "提交拒绝申请";
  await drawer
    .getByRole("button", { name: decisionLabel, exact: true })
    .click();
  await expect(
    drawer.getByRole("button", { name: decisionLabel, exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  if (decision === "reject") {
    await drawer
      .getByLabel("处理原因（必填）")
      .fill("Requested capability is outside this isolated workflow");
  }
  const responsePromise = page.waitForResponse((response) =>
    apiResponse(
      response,
      "POST",
      decision === "approve"
        ? /-requests\/.+:approve$/u
        : /-requests\/.+:reject$/u,
    ),
  );
  await drawer.getByRole("button", { name: submitLabel }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  await expect(drawer).toBeHidden();
  return response;
}

async function configureSchemaAndQuality(
  request: APIRequestContext,
  scope: RealRunScope,
  adminToken: string,
  manifest: Record<string, unknown>,
): Promise<{
  readonly schemaId: string;
  readonly requestIds: readonly string[];
}> {
  const schemaId = `${scope.runId}-tag-schema`;
  const datasetId =
    process.env.HC_WAVE2_DATASET_ID ?? `be22-${scope.runId}-dataset`;
  const snapshotId =
    process.env.HC_WAVE2_DATASET_SCHEMA_SNAPSHOT_ID ?? "be22-dataset-schema-v1";
  const created = await gatewayRequest(request, {
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/tag-schemas`,
    token: adminToken,
    body: {
      schema_id: schemaId,
      version: 1,
      name: "FE11 three-level operation tags",
      document: {
        nodes: [
          {
            tag_id: "operation-stage",
            code: "operation-stage",
            display_name: "Operation stage",
          },
          {
            tag_id: "grab-action",
            code: "grab-action",
            display_name: "Grab action",
            parent_tag_id: "operation-stage",
          },
          {
            tag_id: "grab-success",
            code: "grab-success",
            display_name: "Grab succeeded",
            parent_tag_id: "grab-action",
          },
        ],
        mutual_exclusions: [],
        object_relations: [],
      },
      compatible_targets: [
        {
          region_code: scope.regionCode,
          dataset_id: datasetId,
          dataset_schema_snapshot_id: snapshotId,
          task_kind: "TAGGING",
        },
      ],
    },
  });
  expect(created.status).toBe(201);
  const published = await gatewayRequest(request, {
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/tag-schemas/${encodeURIComponent(schemaId)}/versions/1/publish`,
    token: adminToken,
  });
  expect(published.status).toBe(200);
  const profileId = `${scope.runId}-manifest-30hz`;
  const profile = await gatewayRequest(request, {
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/quality-profiles`,
    token: adminToken,
    body: {
      profile_id: profileId,
      profile_version: 1,
      required_topics: manifest.expected_topics,
      default_timing: { target_frequency_hz: 30 },
      action: {
        minimum_observation_count_risk: 0,
        minimum_observation_count_reject: 0,
      },
    },
  });
  expect(profile.status).toBe(201);
  return {
    schemaId,
    requestIds: compactIds([
      created.requestId,
      published.requestId,
      profile.requestId,
    ]),
  };
}

async function waitForWorker(
  request: APIRequestContext,
  token: string,
  workflowId: string,
): Promise<{
  readonly job: Record<string, unknown>;
  readonly requestIds: readonly string[];
}> {
  const timeout = Number(process.env.HC_REAL_API_WORKER_TIMEOUT_MS ?? 180_000);
  const deadline = Date.now() + timeout;
  const requestIds: string[] = [];
  while (Date.now() < deadline) {
    const response = await gatewayRequest(request, {
      method: "GET",
      path: `/jobs/${encodeURIComponent(workflowId)}`,
      token,
    });
    if (response.requestId) requestIds.push(response.requestId);
    if (response.status === 404) {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 1_000));
      continue;
    }
    expect(response.status).toBe(200);
    const job = objectBody(response.body, "worker job");
    const status = job.status;
    if (
      [
        "SUCCEEDED",
        "TECHNICAL_FAILED",
        "QUALITY_RISK",
        "QUALITY_REJECTED",
        "CANCELLED",
      ].includes(String(status))
    ) {
      expect(status).toBe("SUCCEEDED");
      return { job, requestIds };
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 1_000));
  }
  throw new Error(
    "Worker job did not reach a terminal state within the bounded timeout",
  );
}

test("real API main chain: empty account to idempotently closed collection task", async ({
  browser,
  request,
}) => {
  requireRealEnvironment();
  const scope = createRunScope();
  const stages: Stage[] = [];
  const startedAt = new Date().toISOString();
  const responseStatusCounts = new Map<string, number>();
  let sameOriginApiResponseCount = 0;
  let mockUrlsObserved = 0;
  let mockWorkerRegistrations = 0;
  const objectStoreRequests: MainChainArtifact["network"]["objectStoreRequests"] =
    [];
  const expectedObjectStoreOrigin = new URL(
    process.env.HC_REAL_API_OBJECT_STORE_ORIGIN ?? "http://127.0.0.1:9000",
  ).origin;
  let cleanupBefore: CleanupResult | null = null;
  let cleanupAfter: CleanupResult | null = null;
  let cleanupVerification: CleanupResult | null = null;
  let admin: BrowserIdentity | null = null;
  let contractor: BrowserIdentity | null = null;
  let caught: unknown = null;
  let runStatus: MainChainArtifact["status"] = "RUNNING";

  const observe = (context: BrowserContext) => {
    context.on("response", (response) => {
      const url = new URL(response.url());
      if (/mockServiceWorker|__msw|\/mocks\//iu.test(url.href)) {
        mockUrlsObserved += 1;
      }
      if (url.pathname.startsWith("/api/v1/")) {
        sameOriginApiResponseCount += 1;
        const key = String(response.status());
        responseStatusCounts.set(key, (responseStatusCounts.get(key) ?? 0) + 1);
      }
      if (url.origin === expectedObjectStoreOrigin) {
        const method = response.request().method();
        objectStoreRequests.push({
          origin: url.origin,
          host: url.host,
          method,
          status: response.status(),
          elapsedMs: Math.round(
            Math.max(0, response.request().timing().responseEnd),
          ),
        });
      }
    });
  };
  const addStage = (
    name: string,
    requestIds: readonly (string | null | undefined)[] = [],
    resourceIds: Readonly<Record<string, string>> = {},
  ) => {
    stages.push({
      name,
      status: "PASSED",
      requestIds: compactIds(requestIds),
      resourceIds,
    });
  };

  try {
    cleanupBefore = cleanupRun(scope);
    addStage("pre-cleanup isolated namespace");

    const adminContext = await browser.newContext();
    observe(adminContext);
    admin = await registerAndLogin(adminContext, scope.adminUsername);
    const bootstrapAdminToken = issueBootstrapAdminToken(
      admin.principalId,
      scope,
    );
    const adminMembershipResponse = await submitMembership(
      admin.page,
      scope.projectId,
    );
    const adminMembership = objectBody(
      await adminMembershipResponse.json(),
      "admin membership request",
    );
    const adminMembershipId = stringField(
      adminMembership,
      "request_id",
      "admin membership request",
    );
    const adminMembershipApproval = await approveWithBootstrapToken(
      request,
      bootstrapAdminToken,
      scope.projectId,
      "membership",
      adminMembershipId,
      `${scope.runId}-admin-membership-approve`,
    );
    const adminMembershipBootstrap = await refreshEmptyAccount(
      admin.page,
      "capability",
    );
    const adminCapabilityResponse = await submitCapabilities(
      admin.page,
      scope.projectId,
      ["project.access.manage", "tag_schema.write", "annotation.review"],
    );
    const adminCapability = objectBody(
      await adminCapabilityResponse.json(),
      "admin capability request",
    );
    const adminCapabilityId = stringField(
      adminCapability,
      "request_id",
      "admin capability request",
    );
    const adminCapabilityApproval = await approveWithBootstrapToken(
      request,
      bootstrapAdminToken,
      scope.projectId,
      "capability",
      adminCapabilityId,
      `${scope.runId}-admin-capability-approve`,
    );
    const adminCapabilityBootstrap = await refreshEmptyAccount(
      admin.page,
      "shell",
    );
    const renewedAdminSession = await loginExisting(
      admin.page,
      scope.adminUsername,
      admin.password,
    );
    admin = {
      ...admin,
      token: renewedAdminSession.token,
      requestIds: compactIds([
        ...admin.requestIds,
        ...renewedAdminSession.requestIds,
      ]),
    };
    await selectRegion(admin.page, scope.regionCode);
    addStage(
      "B registers empty, requests membership and capabilities, then enters real scope",
      [
        ...admin.requestIds,
        responseRequestId(adminMembershipResponse),
        adminMembershipApproval.requestId,
        responseRequestId(adminMembershipBootstrap),
        responseRequestId(adminCapabilityResponse),
        adminCapabilityApproval.requestId,
        responseRequestId(adminCapabilityBootstrap),
      ],
      {
        adminPrincipalId: admin.principalId,
        membershipRequestId: adminMembershipId,
        capabilityRequestId: adminCapabilityId,
      },
    );

    const contractorContext = await browser.newContext();
    observe(contractorContext);
    contractor = await registerAndLogin(
      contractorContext,
      scope.contractorUsername,
    );
    const contractorMembershipResponse = await submitMembership(
      contractor.page,
      scope.projectId,
    );
    const contractorMembershipId = stringField(
      objectBody(
        await contractorMembershipResponse.json(),
        "contractor membership request",
      ),
      "request_id",
      "contractor membership request",
    );
    const membershipApprovedInUi = await approveInUi(
      admin.page,
      scope.projectId,
      "membership",
      contractorMembershipId,
    );
    const contractorMembershipBootstrap = await refreshEmptyAccount(
      contractor.page,
      "capability",
    );
    const contractorCapabilityResponse = await submitCapabilities(
      contractor.page,
      scope.projectId,
      ["collection.upload", "annotation.write"],
    );
    const contractorCapabilityId = stringField(
      objectBody(
        await contractorCapabilityResponse.json(),
        "contractor capability request",
      ),
      "request_id",
      "contractor capability request",
    );
    const capabilityApprovedInUi = await approveInUi(
      admin.page,
      scope.projectId,
      "capability",
      contractorCapabilityId,
    );
    const contractorCapabilityBootstrap = await refreshEmptyAccount(
      contractor.page,
      "shell",
    );
    const renewedContractorSession = await loginExisting(
      contractor.page,
      scope.contractorUsername,
      contractor.password,
    );
    contractor = {
      ...contractor,
      token: renewedContractorSession.token,
      requestIds: compactIds([
        ...contractor.requestIds,
        ...renewedContractorSession.requestIds,
      ]),
    };
    await selectRegion(contractor.page, scope.regionCode);
    addStage(
      "A registers empty and B approves A membership and minimum capabilities in P18",
      [
        ...contractor.requestIds,
        responseRequestId(contractorMembershipResponse),
        responseRequestId(membershipApprovedInUi),
        responseRequestId(contractorMembershipBootstrap),
        responseRequestId(contractorCapabilityResponse),
        responseRequestId(capabilityApprovedInUi),
        responseRequestId(contractorCapabilityBootstrap),
      ],
      {
        contractorPrincipalId: contractor.principalId,
        membershipRequestId: contractorMembershipId,
        capabilityRequestId: contractorCapabilityId,
      },
    );

    const rejectedCapabilityRequest = await gatewayRequest(request, {
      method: "POST",
      path: `/projects/${encodeURIComponent(scope.projectId)}/capability-requests`,
      token: contractor.token,
      headers: { "Idempotency-Key": `${scope.runId}-rejected-capability` },
      body: {
        capability_keys: ["datasets.read"],
        reason: "Negative P18 decision proof",
      },
    });
    expect(rejectedCapabilityRequest.status).toBe(201);
    const rejectedCapabilityId = stringField(
      objectBody(rejectedCapabilityRequest.body, "rejected capability request"),
      "request_id",
      "rejected capability request",
    );
    const rejectedInUi = await approveInUi(
      admin.page,
      scope.projectId,
      "capability",
      rejectedCapabilityId,
      "reject",
    );
    expect(
      objectBody(await rejectedInUi.json(), "rejected capability decision")
        .status,
    ).toBe("REJECTED");
    addStage(
      "A submits an additional real capability request and B rejects it in P18",
      [rejectedCapabilityRequest.requestId, responseRequestId(rejectedInUi)],
      { capabilityRequestId: rejectedCapabilityId },
    );

    const positiveListPromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "GET", /\/collection-tasks(?:\?.*)?$/u),
    );
    await navigateInApp(contractor.page, "/collection-tasks");
    const positiveList = await positiveListPromise;
    expect(positiveList.status()).toBe(200);
    const forbidden = await gatewayRequest(request, {
      method: "GET",
      path: `/projects/${encodeURIComponent(scope.foreignProjectId)}/collection-tasks`,
      token: contractor.token,
    });
    expect(forbidden.status).toBe(403);
    expect(
      stringField(
        objectBody(forbidden.body, "403 problem"),
        "code",
        "403 problem",
      ),
    ).toBeTruthy();
    addStage("paired current-project success and foreign-project 403 denial", [
      responseRequestId(positiveList),
      forbidden.requestId,
    ]);

    await contractor.page.getByRole("button", { name: "新建采集任务" }).click();
    const taskDrawer = contractor.page.getByRole("dialog", {
      name: "新建采集任务",
    });
    await taskDrawer
      .getByLabel("任务名称")
      .fill(`FE11 isolated ${scope.runId}`);
    await taskDrawer.getByLabel("采集类型").fill("COLLECTION");
    await taskDrawer.getByLabel("采集场景").fill("fe11-real-browser");
    await taskDrawer
      .getByLabel("任务描述")
      .fill("Deterministic real browser package chain.");
    await taskDrawer.getByLabel("目标数据包数量").fill("1");
    const createResponsePromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/collection-tasks$/u),
    );
    await taskDrawer.getByRole("button", { name: "创建任务" }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status()).toBe(201);
    const collectionTask = objectBody(
      await createResponse.json(),
      "collection task",
    );
    const collectionTaskId = stringField(
      collectionTask,
      "collection_task_id",
      "collection task",
    );
    const createIdempotencyKey = await createResponse
      .request()
      .headerValue("idempotency-key");
    expect(createIdempotencyKey).toBeTruthy();
    const idempotencyConflict = await gatewayRequest(request, {
      method: "POST",
      path: `/projects/${encodeURIComponent(scope.projectId)}/collection-tasks`,
      token: contractor.token,
      headers: { "Idempotency-Key": createIdempotencyKey as string },
      body: {
        name: `changed ${scope.runId}`,
        type: "COLLECTION",
        scenario: "changed-idempotency-body",
        description: "must conflict",
        target: { package_count: 1 },
        quality_threshold: null,
      },
    });
    expect(idempotencyConflict.status).toBe(409);
    expect(
      stringField(
        objectBody(idempotencyConflict.body, "409 problem"),
        "code",
        "409 problem",
      ),
    ).toBeTruthy();
    addStage(
      "A creates P20 without assignment, schedule, pause or Topic fields and pairs 201 with 409",
      [responseRequestId(createResponse), idempotencyConflict.requestId],
      { collectionTaskId },
    );

    const manifest = renderLegalManifest(scope, collectionTaskId);
    const configured = await configureSchemaAndQuality(
      request,
      scope,
      admin.token,
      manifest,
    );
    addStage(
      "B publishes the compatible Tag Schema and quality profile",
      configured.requestIds,
      {
        tagSchemaId: configured.schemaId,
      },
    );

    await navigateInApp(contractor.page, "/ingest/uploads/new");
    const preflightPromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/upload-manifests:preflight$/u),
    );
    await contractor.page.locator("#browser-upload-package").setInputFiles([
      {
        name: "rollout_manifest.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(manifest)),
      },
      {
        name: "recording.mcap",
        mimeType: "application/octet-stream",
        buffer: readFileSync(
          resolve(
            repositoryRoot,
            "backend/tests/system/wave2/data/packages/legal.mcap",
          ),
        ),
      },
    ]);
    const preflight = await preflightPromise;
    expect(preflight.status()).toBe(200);
    await expect(
      contractor.page.getByText("预检通过", { exact: true }),
    ).toBeVisible();
    const sessionPromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/upload-sessions$/u),
    );
    const commitPromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/upload-sessions\/.+:commit-manifest$/u),
    );
    await contractor.page.getByRole("button", { name: "开始上传" }).click();
    const sessionResponse = await sessionPromise;
    expect(sessionResponse.status()).toBe(201);
    const sessionGrant = objectBody(
      await sessionResponse.json(),
      "upload session grant",
    );
    const uploadSession = objectBody(sessionGrant.session, "upload session");
    const uploadSessionId = stringField(
      uploadSession,
      "session_id",
      "upload session",
    );
    const commitResponse = await commitPromise;
    expect(commitResponse.status()).toBe(200);
    const commit = objectBody(await commitResponse.json(), "manifest commit");
    const workflow = objectBody(commit.workflow, "manifest commit workflow");
    const workflowId = stringField(
      workflow,
      "workflow_id",
      "manifest commit workflow",
    );
    await expect(
      contractor.page.getByText("Raw 已提交", { exact: true }),
    ).toBeVisible();
    addStage(
      "A selects the checked-in legal Manifest and MCAP in P03 and commits through the browser",
      [
        responseRequestId(preflight),
        responseRequestId(sessionResponse),
        responseRequestId(commitResponse),
      ],
      { uploadSessionId, workflowId },
    );

    if (process.env.HC_REAL_API_UPLOAD_SMOKE_ONLY === "1") {
      mockWorkerRegistrations =
        (await assertNoMockWorker(admin.page)) +
        (await assertNoMockWorker(contractor.page));
      expect(mockWorkerRegistrations).toBe(0);
      expect(mockUrlsObserved).toBe(0);
      expect(
        objectStoreRequests.some(
          (entry) =>
            entry.method === "PUT" && entry.status >= 200 && entry.status < 300,
        ),
      ).toBe(true);
      addStage(
        "fresh isolated browser upload smoke reaches commit with exact CORS and no mock worker",
      );
      runStatus = "PASSED";
      throw new UploadSmokeComplete();
    }

    const worker = await waitForWorker(request, contractor.token, workflowId);
    const workerResult = objectBody(worker.job.result, "worker result");
    const automaticTask = objectBody(
      workerResult.annotation_task,
      "worker annotation task",
    );
    const annotationTaskId = stringField(
      automaticTask,
      "task_id",
      "worker annotation task",
    );
    const workerJobId = stringField(worker.job, "job_id", "worker job");
    addStage(
      "bounded Worker/QC polling reaches SUCCEEDED and exposes the automatic task",
      worker.requestIds,
      {
        workerJobId,
        annotationTaskId,
      },
    );

    await navigateInApp(
      contractor.page,
      `/ingest/uploads/${encodeURIComponent(uploadSessionId)}`,
    );
    await expect(contractor.page.getByText("自动质检通过")).toBeVisible();
    await expect(
      contractor.page.getByText("front", { exact: true }).first(),
    ).toBeVisible();
    addStage(
      "P04 renders the real PASS summary and Manifest-discovered camera",
    );

    await navigateInApp(contractor.page, "/annotations/annotate");
    const annotationRow = contractor.page
      .getByRole("row")
      .filter({ hasText: String(manifest.rollout_id) });
    await expect(annotationRow).toBeVisible();
    const claimPromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/annotation-tasks\/.+\/claim$/u),
    );
    await annotationRow.getByRole("button", { name: "领取任务" }).click();
    const claimResponse = await claimPromise;
    expect(claimResponse.status()).toBe(200);
    await expect(
      annotationRow.getByRole("button", { name: "打开任务" }),
    ).toBeVisible();
    await annotationRow.getByRole("button", { name: "打开任务" }).click();
    await expect(
      contractor.page.getByRole("heading", { name: "多级 Tag 工具" }),
    ).toBeVisible();
    await contractor.page
      .getByRole("treeitem", { name: /Grab succeeded.*grab-success/u })
      .click();
    await contractor.page
      .getByRole("button", { name: "添加所选 Tag 区间" })
      .click();
    await contractor.page.getByLabel(/结束步（开区间）/u).fill("10");
    const savePromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/annotation-tasks\/.+\/revisions$/u),
    );
    await contractor.page.getByRole("button", { name: "保存草稿" }).click();
    const saveResponse = await savePromise;
    expect(saveResponse.status()).toBe(201);
    const staleEtag = await saveResponse.request().headerValue("if-match");
    const savedBody = saveResponse.request().postDataJSON() as Record<
      string,
      unknown
    >;
    const staleConflict = await gatewayRequest(request, {
      method: "POST",
      path: `/annotation-tasks/${encodeURIComponent(annotationTaskId)}/revisions`,
      token: contractor.token,
      headers: {
        "X-Project-ID": scope.projectId,
        "X-Region-Code": scope.regionCode,
        "If-Match": staleEtag as string,
      },
      body: {
        ...savedBody,
        client_mutation_id: `${scope.runId}-stale-save`,
      },
    });
    expect(staleConflict.status).toBe(409);
    await expect(
      contractor.page.getByRole("button", { name: "提交审核" }),
    ).toBeEnabled();
    const submitPromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/annotation-tasks\/.+\/submit$/u),
    );
    await contractor.page.getByRole("button", { name: "提交审核" }).click();
    await contractor.page
      .getByRole("dialog", { name: "提交 Tag 审核" })
      .getByRole("button", { name: "确认提交审核" })
      .click();
    const submitResponse = await submitPromise;
    expect(submitResponse.status()).toBe(201);
    const submission = objectBody(
      await submitResponse.json(),
      "annotation submission",
    );
    const submissionId = stringField(
      submission,
      "submission_id",
      "annotation submission",
    );
    addStage(
      "A claims the automatic task, saves and submits a three-level Tag, pairing save with stale 409",
      [
        responseRequestId(claimResponse),
        responseRequestId(saveResponse),
        staleConflict.requestId,
        responseRequestId(submitResponse),
      ],
      { annotationTaskId, submissionId },
    );

    await navigateInApp(admin.page, "/annotations/tag-review");
    const reviewRow = admin.page
      .getByRole("row")
      .filter({ hasText: String(manifest.rollout_id) });
    await expect(reviewRow).toBeVisible();
    await reviewRow.getByRole("button", { name: "进入审核" }).click();
    await expect(
      admin.page.getByRole("button", { name: "审核通过" }),
    ).toBeEnabled();
    const reviewPromise = admin.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/annotation-tasks\/.+\/reviews$/u),
    );
    await admin.page.getByRole("button", { name: "审核通过" }).click();
    await admin.page
      .getByRole("dialog", { name: "审核通过" })
      .getByRole("button", { name: "审核通过" })
      .click();
    const reviewResponse = await reviewPromise;
    expect(reviewResponse.status()).toBe(200);
    expect(
      objectBody(await reviewResponse.json(), "annotation review").status,
    ).toBe("APPROVED");
    addStage(
      "distinct administrator B approves the fixed Tag submission in the review workbench",
      [responseRequestId(reviewResponse)],
      { annotationTaskId },
    );

    await navigateInApp(contractor.page, "/collection-tasks");
    const collectionRow = contractor.page
      .getByRole("row")
      .filter({ hasText: `FE11 isolated ${scope.runId}` });
    await expect(collectionRow).toBeVisible();
    await collectionRow.getByRole("button", { name: "关闭任务" }).click();
    const closeResponsePromise = contractor.page.waitForResponse((response) =>
      apiResponse(response, "POST", /\/collection-tasks\/.+:close$/u),
    );
    await contractor.page
      .getByRole("dialog", { name: "关闭采集任务" })
      .getByRole("button", { name: "确认关闭任务" })
      .click();
    const closeResponse = await closeResponsePromise;
    expect(closeResponse.status()).toBe(200);
    const closed = objectBody(
      await closeResponse.json(),
      "closed collection task",
    );
    expect(closed.status).toBe("CLOSED");
    const closeKey = await closeResponse
      .request()
      .headerValue("idempotency-key");
    const closeEtag = await closeResponse.request().headerValue("if-match");
    expect(closeKey).toBeTruthy();
    expect(closeEtag).toBeTruthy();
    const closeReplay = await gatewayRequest(request, {
      method: "POST",
      path: `/projects/${encodeURIComponent(scope.projectId)}/collection-tasks/${encodeURIComponent(collectionTaskId)}:close`,
      token: contractor.token,
      headers: {
        "Idempotency-Key": closeKey as string,
        "If-Match": closeEtag as string,
      },
    });
    expect(closeReplay.status).toBe(200);
    expect(closeReplay.body).toEqual(closed);
    addStage(
      "A closes P20 in the UI and the exact gateway replay remains idempotently CLOSED",
      [responseRequestId(closeResponse), closeReplay.requestId],
      { collectionTaskId, status: "CLOSED" },
    );

    mockWorkerRegistrations =
      (await assertNoMockWorker(admin.page)) +
      (await assertNoMockWorker(contractor.page));
    expect(mockWorkerRegistrations).toBe(0);
    expect(mockUrlsObserved).toBe(0);
    addStage(
      "both browser contexts remain free of MSW and mock Service Workers",
    );

    if (process.env.HC_REAL_API_EXPECT_429 !== "1") {
      stages.push({
        name: "paired positive and externally rate-limited 429 authentication requests",
        status: "NOT_RUN",
        requestIds: [],
        resourceIds: {},
      });
      throw new Error(
        "429 paired network assertion NOT RUN: enable the deployment abuse policy and HC_REAL_API_EXPECT_429=1",
      );
    }
    const maxRateAttempts = Number(
      process.env.HC_REAL_API_429_MAX_ATTEMPTS ?? 50,
    );
    let limitedRate: SafeGatewayResponse | null = null;
    const rateRequestIds: string[] = [...contractor.requestIds];
    for (let attempt = 0; attempt < maxRateAttempts; attempt += 1) {
      const response = await gatewayRequest(request, {
        method: "POST",
        path: "/auth/sessions",
        body: {
          username: scope.contractorUsername,
          password: contractor.password,
        },
      });
      if (response.requestId) rateRequestIds.push(response.requestId);
      if (response.status === 429) {
        limitedRate = response;
        break;
      }
      expect(response.status).toBe(201);
    }
    expect(
      limitedRate,
      "external abuse policy did not return 429 within the bound",
    ).not.toBeNull();
    expect(
      stringField(
        objectBody(limitedRate?.body, "429 problem"),
        "code",
        "429 problem",
      ),
    ).toBeTruthy();
    addStage(
      "paired positive and externally rate-limited 429 authentication requests",
      rateRequestIds,
    );
    runStatus = "PASSED";
  } catch (error) {
    if (!(error instanceof UploadSmokeComplete)) {
      caught = error;
      runStatus = "FAILED";
    }
  } finally {
    for (const identity of [admin, contractor]) {
      if (!identity) continue;
      try {
        await identity.context.close();
      } catch (error) {
        caught ??= error;
        runStatus = "FAILED";
      }
    }
    try {
      cleanupAfter = cleanupRun(scope);
      cleanupVerification = cleanupRun(scope);
      if (totalCleanupCount(cleanupVerification) !== 0) {
        throw new Error(
          "Strict cleanup verification found remaining database rows or objects",
        );
      }
    } catch (error) {
      caught ??= error;
      runStatus = "FAILED";
    }
    const artifact: MainChainArtifact = {
      schemaVersion: 1,
      runId: scope.runId,
      projectId: scope.projectId,
      foreignProjectId: scope.foreignProjectId,
      regionCode: scope.regionCode,
      status: runStatus,
      startedAt,
      finishedAt: new Date().toISOString(),
      stages,
      network: {
        sameOriginApiResponseCount,
        responseStatusCounts: Object.fromEntries(
          [...responseStatusCounts.entries()].sort(([left], [right]) =>
            left.localeCompare(right),
          ),
        ),
        objectStoreRequests,
        mockWorkerRegistrations,
        mockUrlsObserved,
        trace: "OFF_CREDENTIAL_SAFETY",
        video: "OFF_CREDENTIAL_SAFETY",
      },
      cleanup: {
        before: cleanupBefore,
        after: cleanupAfter,
        verification: cleanupVerification,
      },
    };
    writeSecretFreeArtifact(artifact);
  }

  if (caught) throw caught;
  expect(runStatus).toBe("PASSED");
});
