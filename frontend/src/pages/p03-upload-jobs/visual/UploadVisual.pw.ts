import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

interface AxeResult {
  violations: readonly unknown[];
}

const artifactRoot = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture/E05"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe15"
      ? "../artifacts/visual/e01-e10/FE15-fixes/E05"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe13"
        ? "../artifacts/visual/e01-e10/FE13-fixes/E05"
        : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
          ? "../artifacts/visual/e01-e10/FE12-final/E05"
          : "../artifacts/visual/e01-e10/E05",
);
const projectId = "project_e02_visual";
const regionCode = "cn-east-01";
const rawSize = 16 * 1024 * 1024;
const recoveredSize = 60 * 1024 * 1024;
const sha256 = "9b".repeat(32);
const recoveredSha256 = "4d".repeat(32);

function manifestFixture(options: {
  readonly packageId: string;
  readonly rolloutId: string;
  readonly fileName: string;
  readonly fileSize: number;
  readonly digest: string;
}) {
  return {
    schema_version: 1,
    project_id: projectId,
    task_id: "task_palletizing_042",
    collection_job_id: "collection_job_042",
    rollout_id: options.rolloutId,
    collection_session_id: "collection_session_20260818_042",
    recording_request_id: "recording_request_042",
    data_package_id: options.packageId,
    sequence_no: 42,
    robot_id: "robot_hc_xh_042",
    pico_instance_id: "pico_hc_042",
    start_time: "2026-08-18T04:12:20Z",
    end_time: "2026-08-18T04:14:27Z",
    cameras: [
      {
        camera_id: "wrist_left",
        topic: "/camera/wrist_left/image",
        frame_id: "wrist_left_optical",
        encoding: "h264",
      },
      {
        camera_id: "wrist_right",
        topic: "/camera/wrist_right/image",
        frame_id: "wrist_right_optical",
        encoding: "h264",
      },
      {
        camera_id: "overhead",
        topic: "/camera/overhead/image",
        frame_id: "overhead_optical",
        encoding: "h264",
      },
    ],
    topics: [
      {
        name: "/camera/wrist_left/image",
        required: true,
        message_encoding: "cdr",
        schema_name: "sensor_msgs/Image",
      },
      {
        name: "/camera/wrist_right/image",
        required: true,
        message_encoding: "cdr",
        schema_name: "sensor_msgs/Image",
      },
      {
        name: "/camera/overhead/image",
        required: false,
        message_encoding: "cdr",
        schema_name: "sensor_msgs/Image",
      },
      {
        name: "/joint_states",
        required: true,
        message_encoding: "cdr",
        schema_name: "sensor_msgs/JointState",
      },
      {
        name: "/tf",
        required: true,
        message_encoding: "cdr",
        schema_name: "tf2_msgs/TFMessage",
      },
    ],
    expected_topics: [
      "/camera/wrist_left/image",
      "/camera/wrist_right/image",
      "/joint_states",
      "/tf",
      "/force_torque",
    ],
    actual_topics: [
      "/camera/wrist_left/image",
      "/camera/wrist_right/image",
      "/camera/overhead/image",
      "/joint_states",
      "/tf",
    ],
    files: [
      {
        path: `raw/${options.fileName}`,
        size: options.fileSize,
        sha256: options.digest,
        crc64: "824633720832",
        media_type: "application/octet-stream",
        role: "RAW_MCAP",
      },
    ],
    file_size: options.fileSize,
    sha256: options.digest,
    crc64: "824633720832",
    compression: "none",
    recorder_version: "hc-recorder/3.8.2",
  } as const;
}

function preflightFixture(manifest: ReturnType<typeof manifestFixture>) {
  return {
    schema_version: "manifest-preflight/v1",
    manifest_fingerprint: "f1".repeat(32),
    identifiers: {
      collection_session_id: manifest.collection_session_id,
      recording_request_id: manifest.recording_request_id,
      data_package_id: manifest.data_package_id,
      robot_id: manifest.robot_id,
      pico_instance_id: manifest.pico_instance_id,
    },
    time_range: {
      start_time: manifest.start_time,
      end_time: manifest.end_time,
    },
    files: manifest.files,
    total_file_size: manifest.file_size,
    discovery: {
      source: "MANIFEST",
      read_only: true,
      cameras: manifest.cameras,
      topics: manifest.topics,
      missing_expected_topics: [],
    },
    manifest,
  } as const;
}

const selectedManifest = manifestFixture({
  packageId: "pkg_hc_20260818_042",
  rolloutId: "rollout_hc_042",
  fileName: "rollout_042.mcap",
  fileSize: rawSize,
  digest: sha256,
});
const selectedPreflight = preflightFixture(selectedManifest);
const recoveredManifest = manifestFixture({
  packageId: "pkg_hc_20260818_037",
  rolloutId: "rollout_hc_037",
  fileName: "rollout_037.mcap",
  fileSize: recoveredSize,
  digest: recoveredSha256,
});
const recoveredPreflight = preflightFixture(recoveredManifest);
const sessionId = "upload_session_e05_paused";
const timeoutSessionId = "upload_session_e05_timeout";
const session = {
  session_id: sessionId,
  project_id: projectId,
  region_code: regionCode,
  data_package_id: recoveredManifest.data_package_id,
  rollout_id: recoveredManifest.rollout_id,
  source_type: "BROWSER_MULTIPART",
  status: "PAUSED",
  manifest_fingerprint: recoveredPreflight.manifest_fingerprint,
  object_key: `raw/v1/project=${projectId}/region=${regionCode}/${recoveredManifest.data_package_id}/rollout_037.mcap`,
  multipart_upload_id: "multipart_e05_visual",
  expected_size: recoveredSize,
  expected_sha256: recoveredSha256,
  expected_crc64: recoveredManifest.crc64,
  failure_code: null,
  etag: null,
  completed_at: null,
  created_at: "2026-08-18T04:02:00Z",
  updated_at: "2026-08-18T04:08:30Z",
  workflow: null,
} as const;

function partFixture(partNumber: number) {
  return {
    session_id: sessionId,
    project_id: projectId,
    region_code: regionCode,
    part_number: partNumber,
    status: "UPLOADED",
    etag: `etag-e05-${partNumber}`,
    size: 5 * 1024 * 1024,
    crc64: String(1000 + partNumber),
    retry_count: 0,
    failure_code: null,
    authorization_expires_at: null,
    updated_at: "2026-08-18T04:08:30Z",
  } as const;
}

async function mockFormalUploadApi(
  page: Page,
  scenario: "reference" | "timeout",
): Promise<void> {
  const resourceRoot = `/api/v1/projects/${projectId}/regions/${regionCode}`;
  let timeoutUploaded = false;
  await page.route(
    "**/visual-object-store/e05-timeout-part-1",
    async (route) => {
      timeoutUploaded = true;
      await route.fulfill({ status: 200, body: "" });
    },
  );
  await page.route(`**${resourceRoot}/upload-sessions**`, async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "GET" && pathname.endsWith("/upload-sessions")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: scenario === "timeout" ? [] : [session],
          total: scenario === "timeout" ? 0 : 1,
        }),
      });
      return;
    }
    if (
      request.method() === "GET" &&
      pathname.endsWith(`/${sessionId}/manifest`)
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recoveredPreflight),
      });
      return;
    }
    if (
      request.method() === "GET" &&
      pathname.endsWith(`/${sessionId}/parts`)
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([1, 2, 3, 4].map(partFixture)),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/problem+json",
      body: JSON.stringify({
        title: `visual route missing: ${request.method()} ${pathname}`,
        status: 404,
      }),
    });
  });
  await page.route(
    `**${resourceRoot}/upload-manifests:preflight`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(selectedPreflight),
      });
    },
  );
  await page.route(
    `**${resourceRoot}/upload-sessions/${timeoutSessionId}`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...session,
          session_id: timeoutSessionId,
          data_package_id: "pkg_hc_e05_timeout",
          status: "FAILED",
          expected_size: 5 * 1024 * 1024,
          failure_code: "PART_TIMEOUT",
        }),
      });
    },
  );
  await page.route(
    `**${resourceRoot}/upload-sessions/${timeoutSessionId}/parts`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          timeoutUploaded
            ? [
                {
                  ...partFixture(1),
                  session_id: timeoutSessionId,
                  size: 5 * 1024 * 1024,
                },
              ]
            : [],
        ),
      });
    },
  );
  await page.route(
    `**${resourceRoot}/upload-sessions/${timeoutSessionId}:retry-parts`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            part_number: 1,
            url: "http://127.0.0.1:5193/visual-object-store/e05-timeout-part-1",
            expires_at: "2026-08-18T05:10:00Z",
          },
        ]),
      });
    },
  );
  await page.route(
    `**${resourceRoot}/upload-sessions/${timeoutSessionId}:complete`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...session,
          session_id: timeoutSessionId,
          data_package_id: "pkg_hc_e05_timeout",
          status: "MULTIPART_COMPLETED",
          expected_size: 5 * 1024 * 1024,
          failure_code: null,
        }),
      });
    },
  );
  await page.route(
    `**${resourceRoot}/upload-sessions/${timeoutSessionId}:commit-manifest`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data_package_id: "pkg_hc_e05_timeout",
          rollout_id: "rollout_e05_timeout",
          object_key: "raw/recording-timeout.mcap",
          status: "RAW_COMMITTED",
        }),
      });
    },
  );
}

async function mountPage(
  page: Page,
  scenario: "reference" | "timeout" = "reference",
): Promise<void> {
  await mockFormalUploadApi(page, scenario);
  await page.goto("/dashboard");
  await page.locator("#main-content").waitFor({ state: "attached" });
  await page.evaluate(async (fixtureScenario) => {
    const main = document.getElementById("main-content");
    if (!main) throw new Error("Missing PlatformShell main content");
    main.setAttribute("role", "presentation");
    for (const child of Array.from(main.children)) {
      child.setAttribute("hidden", "");
      (child as HTMLElement).style.display = "none";
    }
    const host = document.createElement("div");
    host.dataset.e05VisualHost = "true";
    main.append(host);
    type UploadVisualModule = {
      mountUploadVisualFixture(
        element: HTMLElement,
        options: { readonly scenario: "reference" | "timeout" },
      ): void;
    };
    const load = new Function(
      'return import("/src/pages/p03-upload-jobs/testing/UploadVisualFixture.tsx")',
    ) as () => Promise<UploadVisualModule>;
    (await load()).mountUploadVisualFixture(host, {
      scenario: fixtureScenario,
    });
  }, scenario);
  await expect(
    page.getByRole("heading", { level: 1, name: "数据上传" }),
  ).toBeVisible();
  await expect(
    page.getByText(scenario === "timeout" ? "传输失败" : "传输已暂停", {
      exact: true,
    }),
  ).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function selectValidPackage(page: Page): Promise<void> {
  await page.locator("#browser-upload-package").setInputFiles([
    {
      name: "rollout_manifest.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(selectedManifest)),
    },
    {
      name: "rollout_042.mcap",
      mimeType: "application/octet-stream",
      buffer: Buffer.alloc(rawSize, 7),
    },
  ]);
  await expect(page.getByText("预检通过")).toBeVisible();
  await expect(page.getByRole("button", { name: "开始上传" })).toBeEnabled();
}

async function expectLayout(page: Page): Promise<void> {
  const panels = await Promise.all([
    page
      .locator('section[aria-labelledby="upload-method-heading"]')
      .boundingBox(),
    page
      .locator('section[aria-labelledby="manifest-preflight-heading"]')
      .boundingBox(),
    page
      .locator('section[aria-labelledby="upload-queue-heading"]')
      .boundingBox(),
  ]);
  expect(panels.every(Boolean)).toBe(true);
  const widths = panels.map((panel) => panel?.width ?? 0);
  const total = widths.reduce((sum, width) => sum + width, 0);
  [0.25, 0.5, 0.25].forEach((ratio, index) => {
    expect(Math.abs((widths[index] ?? 0) / total - ratio)).toBeLessThan(0.035);
  });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function runAxe(page: Page, testInfo: TestInfo, name: string) {
  const axePath = process.env.AXE_CORE_PATH;
  if (!axePath)
    throw new Error("AXE_CORE_PATH is required for FE13 visual checks");
  await page.addScriptTag({ path: axePath });
  const result = await page.evaluate(async () => {
    const axe = (
      window as Window & { axe?: { run(root: Element): Promise<AxeResult> } }
    ).axe;
    const root = document.querySelector('[data-e05-visual-host="true"]');
    if (!axe || !root) throw new Error("axe-core or E05 root did not load");
    return axe.run(root);
  });
  const report = JSON.stringify({ violations: result.violations }, null, 2);
  writeFileSync(resolve(artifactRoot, `axe-${name}.json`), report);
  await testInfo.attach(`axe-${name}`, {
    body: report,
    contentType: "application/json",
  });
  expect(result.violations).toEqual([]);
}

test.beforeAll(() => mkdirSync(artifactRoot, { recursive: true }));

test("E05 reference layout at 1440x900 and 1280x800", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountPage(page);
  await selectValidPackage(page);
  await expectLayout(page);
  await expect(page.getByText("来源：Manifest · 只读")).toBeVisible();
  await expect(
    page.getByText("上传传输已暂停；采集任务状态没有改变。"),
  ).toBeVisible();
  const newUploadTab = page.getByRole("tab", { name: "新建上传" });
  await newUploadTab.focus();
  expect(
    await newUploadTab.evaluate((element) => {
      const style = getComputedStyle(element);
      return (
        style.outlineStyle !== "none" &&
        Number.parseFloat(style.outlineWidth) >= 2
      );
    }),
  ).toBe(true);
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "上传记录" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.keyboard.press("ArrowLeft");
  await expect(newUploadTab).toHaveAttribute("aria-selected", "true");
  await expect(newUploadTab).toBeFocused();
  await page.evaluate(() =>
    (document.activeElement as HTMLElement | null)?.blur(),
  );
  await page.screenshot({
    path: resolve(artifactRoot, "1440x900.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  await expectLayout(page);
  await page.screenshot({
    path: resolve(artifactRoot, "1280x800.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("E05 empty Manifest is explicit and leaves submission closed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountPage(page);
  await page.locator("#browser-upload-package").setInputFiles([
    {
      name: "rollout_manifest.json",
      mimeType: "application/json",
      buffer: Buffer.from(""),
    },
    {
      name: "rollout_042.mcap",
      mimeType: "application/octet-stream",
      buffer: Buffer.alloc(16, 1),
    },
  ]);
  await expect(page.getByText("未发现可用 Manifest")).toBeVisible();
  await expect(page.getByText("MANIFEST_EMPTY")).toBeVisible();
  await expect(page.getByRole("button", { name: "开始上传" })).toBeDisabled();
  await page.screenshot({
    path: resolve(artifactRoot, "1440x900-empty-manifest.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("E05 form semantics, keyboard focus, long URI and axe", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountPage(page);
  await expect(page.locator("#browser-upload-package")).toHaveAttribute(
    "name",
    "browser-upload-package",
  );

  await page.getByRole("radio", { name: /授权对象地址/u }).click();
  const uri = page.getByLabel("已授权对象地址");
  await expect(uri).toHaveAttribute("name", "object-storage-uri");
  await expect(uri).toHaveAttribute("autocomplete", "off");
  await expect(uri).toHaveAttribute(
    "placeholder",
    "s3://受管存储桶/raw/v1/…/recording.mcap",
  );
  await expect(page.locator("#object-upload-manifest")).toHaveAttribute(
    "name",
    "object-upload-manifest",
  );
  await uri.fill(
    "s3://managed-bucket/raw/v1/project-fe13/a-very-long-canonical-path-that-must-remain-inside-the-upload-column/recording.mcap",
  );
  await uri.focus();
  await expect(uri).toBeFocused();
  expect(
    await uri.evaluate((element) => {
      const wrapper = element.closest(".ant-input");
      const target = wrapper instanceof HTMLElement ? wrapper : element;
      const style = getComputedStyle(target);
      return style.outlineStyle !== "none" || style.boxShadow !== "none";
    }),
  ).toBe(true);
  await expectLayout(page);
  await runAxe(page, testInfo, "1280x800-object-reference");
  await page.evaluate(() =>
    (document.activeElement as HTMLElement | null)?.blur(),
  );
  await page.screenshot({
    path: resolve(artifactRoot, "1280x800-object-reference.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("E05 timeout remains recoverable and commits only after explicit failed-part retry", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountPage(page, "timeout");
  await expectLayout(page);
  await expect(page.getByText("PART_TIMEOUT")).toBeVisible();
  await expect(page.getByText(/分片 #1 在 120 秒内未完成传输/u)).toBeVisible();
  await expect(page.getByText(/已确认分片不会重复上传/u)).toBeVisible();
  await expect(page.getByText(/请求 ID/u)).toHaveCount(0);
  const retry = page.getByRole("button", { name: "重试失败分片" });
  await retry.focus();
  await expect(retry).toBeFocused();
  await page.screenshot({
    path: resolve(artifactRoot, "1440x900-timeout.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  await expectLayout(page);
  await runAxe(page, testInfo, "1280x800-timeout");
  await page.screenshot({
    path: resolve(artifactRoot, "1280x800-timeout.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalOverflow(page);
  await expect(retry).toBeVisible();
  await page.getByText("PART_TIMEOUT").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: resolve(artifactRoot, "375x812-timeout.png"),
    animations: "disabled",
    fullPage: false,
  });

  await retry.click();
  await expect(page.getByText("Raw 已提交", { exact: true })).toBeVisible();
  await expect(page.getByText("PART_TIMEOUT")).toHaveCount(0);
  await expect(page.getByText("已进入摄取工作流")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: resolve(artifactRoot, "375x812-retry-success.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await expectLayout(page);
  await page.screenshot({
    path: resolve(artifactRoot, "1440x900-retry-success.png"),
    animations: "disabled",
    fullPage: false,
  });
});
