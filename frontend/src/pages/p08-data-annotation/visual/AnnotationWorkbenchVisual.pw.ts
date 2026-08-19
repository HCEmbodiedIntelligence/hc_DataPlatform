import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

interface AxeResult {
  violations: readonly unknown[];
}

const artifactRoot = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe13"
      ? "../artifacts/visual/e01-e10/FE13-fixes"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
        ? "../artifacts/visual/e01-e10/FE12-final"
        : "../artifacts/visual/e01-e10",
);

async function mountFixture(
  page: Page,
  options: {
    readonly mode: "annotation" | "tag-review";
    readonly cameraCount?: number;
    readonly scenario?: "reference" | "empty-cameras" | "conflict";
  },
): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("hc-platform-navigation-collapsed", "true");
  });
  await page.goto("/storage/overview?mockScenario=management%3Ahappy");
  await page.locator("#main-content").waitFor({ state: "attached" });
  await page.evaluate(async (fixtureOptions) => {
    const main = document.getElementById("main-content");
    if (!main) throw new Error("Missing PlatformShell main content");
    for (const child of Array.from(main.children))
      child.setAttribute("hidden", "");
    const host = document.createElement("div");
    host.dataset.p08VisualHost = "true";
    main.append(host);
    type VisualModule = {
      mountAnnotationVisualFixture(
        element: HTMLElement,
        value: {
          readonly mode: "annotation" | "tag-review";
          readonly cameraCount?: number;
          readonly scenario?: "reference" | "empty-cameras" | "conflict";
        },
      ): void;
    };
    const load = new Function(
      'return import("/src/pages/p08-data-annotation/testing/AnnotationVisualFixture.tsx")',
    ) as () => Promise<VisualModule>;
    const module = await load();
    module.mountAnnotationVisualFixture(host, fixtureOptions);
  }, options);
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: options.mode === "annotation" ? "数据标注" : "Tag 审核",
    }),
  ).toBeVisible();
}

async function mountQueueFixture(page: Page): Promise<void> {
  await page.route(
    "**/api/v1/projects/project_fe13_visual/annotation-tasks**",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            task_id:
              "annotation-task-fe13-with-a-deliberately-long-auditable-identifier",
            project_id: "project_fe13_visual",
            region_code: "cn-east-01",
            dataset_id:
              "dataset-fe13-with-a-very-long-name-that-must-remain-contained",
            dataset_version: 18,
            rollout_id:
              "rollout-fe13-production-cell-with-an-extremely-long-readable-name",
            base_lance_version: 42,
            base_step_count: 26787,
            tag_schema_id: "robot-operation-schema-fe13",
            tag_schema_version: 7,
            task_kind: "TAGGING",
            creation_source: "SYSTEM_LANCE",
            source_workflow_id: "ingest/fe13/visual",
            assignee_id: null,
            current_revision: 0,
            state_version: 1,
            current_submission_id: null,
            submitted_revision: null,
            submitted_by: null,
            approved_revision: null,
            approved_review_id: null,
            status: "DRAFT",
            schema_version: "1",
            etag: '"annotation-task-fe13-v1"',
            created_at: "2026-08-18T08:00:00Z",
            updated_at: "2026-08-18T08:42:00Z",
          },
        ]),
      });
    },
  );
  await page.goto("/dashboard");
  await page.locator("#main-content").waitFor({ state: "attached" });
  await page.evaluate(async () => {
    const main = document.getElementById("main-content");
    if (!main) throw new Error("Missing PlatformShell main content");
    main.setAttribute("role", "presentation");
    for (const child of Array.from(main.children))
      child.setAttribute("hidden", "");
    const host = document.createElement("div");
    host.dataset.fe13AnnotationQueueHost = "true";
    main.append(host);
    type QueueVisualModule = {
      mountAnnotationQueueVisualFixture(
        element: HTMLElement,
        mode: "annotation" | "tag-review",
      ): void;
    };
    const load = new Function(
      'return import("/src/pages/p08-data-annotation/testing/AnnotationQueueVisualFixture.tsx")',
    ) as () => Promise<QueueVisualModule>;
    (await load()).mountAnnotationQueueVisualFixture(host, "annotation");
  });
  await expect(
    page.getByRole("heading", { level: 1, name: "数据标注" }),
  ).toBeVisible();
  await expect(page.getByText(/rollout-fe13-production-cell/u)).toBeVisible();
}

async function runAxe(
  page: Page,
  testInfo: TestInfo,
  rootSelector: string,
  outputPath: string,
) {
  const axePath = process.env.AXE_CORE_PATH;
  if (!axePath)
    throw new Error("AXE_CORE_PATH is required for FE13 visual checks");
  await page.addScriptTag({ path: axePath });
  const result = await page.evaluate(
    async ({ selector }) => {
      const axe = (
        window as Window & { axe?: { run(root: Element): Promise<AxeResult> } }
      ).axe;
      const root = document.querySelector(selector);
      if (!axe || !root) throw new Error("axe-core or audit root did not load");
      return axe.run(root);
    },
    { selector: rootSelector },
  );
  const report = JSON.stringify({ violations: result.violations }, null, 2);
  writeFileSync(outputPath, report);
  await testInfo.attach("axe-fe13-annotation-queue", {
    body: report,
    contentType: "application/json",
  });
  expect(result.violations).toEqual([]);
}

async function expectGeometry(
  page: Page,
  mode: "annotation" | "tag-review",
): Promise<void> {
  const boxes = await Promise.all([
    page.getByLabel("采集条目导航").boundingBox(),
    page.getByLabel("相机与同步信号").boundingBox(),
    page.getByLabel("模式工具与发现").boundingBox(),
  ]);
  expect(boxes.every(Boolean)).toBe(true);
  const widths = boxes.map((box) => box?.width ?? 0);
  const total = widths.reduce((sum, width) => sum + width, 0);
  const expected =
    mode === "annotation" ? [0.18, 0.58, 0.24] : [0.18, 0.54, 0.28];
  widths.forEach((width, index) =>
    expect(Math.abs(width / total - (expected[index] ?? 0))).toBeLessThan(
      0.035,
    ),
  );
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.getByRole("slider", { name: "共享播放位置" })).toHaveCount(
    1,
  );
}

for (const mode of ["annotation", "tag-review"] as const) {
  const effect = mode === "annotation" ? "E07" : "E08";
  for (const viewport of [
    { width: 1440, height: 900, file: "1440x900.png" },
    { width: 1280, height: 800, file: "1280x800.png" },
  ] as const) {
    test(`${effect} ${mode} at ${viewport.width}x${viewport.height}`, async ({
      page,
    }) => {
      const directory = resolve(artifactRoot, effect);
      mkdirSync(directory, { recursive: true });
      await page.setViewportSize(viewport);
      await mountFixture(page, { mode, cameraCount: 4, scenario: "reference" });
      await expectGeometry(page, mode);
      if (mode === "annotation") {
        await expect(page.getByText("多级 Tag 工具")).toBeVisible();
        await expect(
          page.getByRole("button", { name: "保存草稿" }),
        ).toBeVisible();
        await expect(
          page.getByRole("button", { name: "提交审核" }),
        ).toBeVisible();
        await expect(
          page.getByLabel("Manifest 相机视图").locator(".viewer-panel"),
        ).toHaveCount(4);
      } else {
        await expect(page.getByText("六项审核清单")).toBeVisible();
        await expect(page.getByText("原始 / 修订差异")).toBeVisible();
        await expect(
          page.getByLabel("Manifest 相机视图").locator(".viewer-panel"),
        ).toHaveCount(1);
        await expect(page.locator("#review-camera-select option")).toHaveCount(
          4,
        );
      }
      await page.screenshot({
        path: resolve(directory, viewport.file),
        fullPage: false,
      });
    });
  }
}

test("E07 keeps the workbench skeleton for a zero-camera Manifest", async ({
  page,
}) => {
  const directory = resolve(artifactRoot, "E07");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, { mode: "annotation", scenario: "empty-cameras" });
  await expect(page.getByText(/Manifest 未发现相机/)).toBeVisible();
  await expect(page.getByRole("slider", { name: "共享播放位置" })).toHaveCount(
    1,
  );
  await page.screenshot({
    path: resolve(directory, "1280x800-empty-cameras.png"),
    fullPage: false,
  });
});

test("E07 adapts a one-camera Manifest without adding another timeline", async ({
  page,
}) => {
  const directory = resolve(artifactRoot, "E07");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 1,
    scenario: "reference",
  });
  await expectGeometry(page, "annotation");
  await expect(
    page.getByLabel("Manifest 相机视图").locator(".viewer-panel"),
  ).toHaveCount(1);
  await page.screenshot({
    path: resolve(directory, "1280x800-one-camera.png"),
    fullPage: false,
  });
});

test("E08 keeps the fixed submission visible after a concurrent 409", async ({
  page,
}) => {
  const directory = resolve(artifactRoot, "E08");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, { mode: "tag-review", scenario: "conflict" });
  await expect(page.getByText("并发版本冲突，写操作已暂停")).toBeVisible();
  await expect(page.getByText("必填属性完整", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "审核通过" })).toBeDisabled();
  await page.screenshot({
    path: resolve(directory, "1280x800-conflict.png"),
    fullPage: false,
  });
});

test("E07 8-camera shared-clock interaction stays within the local render budget", async ({
  page,
}, testInfo) => {
  const directory = resolve(artifactRoot, "E07");
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 8,
    scenario: "reference",
  });
  const profile = await page.evaluate(async () => {
    await new Promise((resolveWait) => window.setTimeout(resolveWait, 350));
    const entries: PerformanceEntry[] = [];
    const observer =
      typeof PerformanceObserver === "undefined"
        ? null
        : new PerformanceObserver((list) => entries.push(...list.getEntries()));
    observer?.observe({ type: "longtask" });
    const slider = document.querySelector<HTMLElement>(
      '[role="slider"][aria-label="共享播放位置"]',
    );
    if (!slider) throw new Error("Missing shared timeline slider");
    slider.focus();
    const started = performance.now();
    for (let index = 0; index < 120; index += 1) {
      slider.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
      );
    }
    const dispatchMs = performance.now() - started;
    await new Promise((resolveWait) => window.setTimeout(resolveWait, 250));
    observer?.disconnect();
    return {
      dispatchMs,
      longTaskCount: entries.length,
      longTaskDurationMs: entries.reduce(
        (sum, entry) => sum + entry.duration,
        0,
      ),
      cameraPanels: document.querySelectorAll(".viewer-panel").length,
      sharedTimelineCount: document.querySelectorAll(
        '[role="slider"][aria-label="共享播放位置"]',
      ).length,
    };
  });
  mkdirSync(directory, { recursive: true });
  const serializedProfile = JSON.stringify(profile, null, 2);
  writeFileSync(
    resolve(directory, "8-camera-performance-profile.json"),
    serializedProfile,
  );
  await testInfo.attach("8-camera-performance-profile", {
    body: serializedProfile,
    contentType: "application/json",
  });
  expect(profile.cameraPanels).toBe(8);
  expect(profile.sharedTimelineCount).toBe(1);
  expect(profile.dispatchMs).toBeLessThan(120);
  expect(profile.longTaskCount).toBeLessThanOrEqual(1);
});

test("E07 exposes the primary workbench controls to the keyboard", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 4,
    scenario: "reference",
  });
  const tagTree = page.getByRole("tree", { name: "多级 Tag Schema 层级" });
  const firstTag = tagTree.getByRole("treeitem").first();
  await firstTag.focus();
  await expect(firstTag).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  const slider = page.getByRole("slider", { name: "共享播放位置" });
  await slider.focus();
  await expect(slider).toBeFocused();
  await page.keyboard.press("ArrowRight");
});

test("E07 queue uses native links, stable search semantics and contained long text", async ({
  page,
}, testInfo) => {
  const directory = resolve(artifactRoot, "E07");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountQueueFixture(page);

  const modeNavigation = page.getByRole("navigation", {
    name: "数据标注功能模式",
  });
  const annotationLink = modeNavigation.getByRole("link", { name: "数据标注" });
  const reviewLink = modeNavigation.getByRole("link", { name: "Tag 审核" });
  await expect(annotationLink).toHaveAttribute("href", "/annotations/annotate");
  await expect(annotationLink).toHaveAttribute("aria-current", "page");
  await expect(reviewLink).toHaveAttribute("href", "/annotations/tag-review");
  expect(
    await reviewLink.evaluate(
      (element) =>
        element instanceof HTMLAnchorElement &&
        element.href.endsWith("/annotations/tag-review"),
    ),
  ).toBe(true);

  const search = page.getByLabel("搜索任务、Rollout、Dataset 或 Schema");
  await expect(search).toHaveAttribute("name", "annotation-task-search");
  await expect(search).toHaveAttribute("autocomplete", "off");
  await expect(search).toHaveAttribute("placeholder", "输入关键词…");
  await annotationLink.focus();
  await expect(annotationLink).toBeFocused();
  expect(
    await annotationLink.evaluate((element) => {
      const style = getComputedStyle(element);
      return (
        style.outlineStyle !== "none" &&
        Number.parseFloat(style.outlineWidth) >= 2
      );
    }),
  ).toBe(true);
  await page.keyboard.press("Tab");
  await expect(reviewLink).toBeFocused();

  const overflow = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(overflow.content).toBeLessThanOrEqual(overflow.viewport);
  await runAxe(
    page,
    testInfo,
    '[data-fe13-annotation-queue-host="true"]',
    resolve(directory, "axe-1280x800-queue.json"),
  );
  await page.evaluate(() =>
    (document.activeElement as HTMLElement | null)?.blur(),
  );
  await page.screenshot({
    path: resolve(directory, "1280x800-queue-links.png"),
    animations: "disabled",
    fullPage: false,
  });
});
