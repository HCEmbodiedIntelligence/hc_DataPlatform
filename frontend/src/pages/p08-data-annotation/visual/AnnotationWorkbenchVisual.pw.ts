import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

interface AxeResult {
  violations: readonly unknown[];
}

const artifactRoot = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe16"
    ? "../artifacts/visual/e01-e10/FE16-final/fixture"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
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
  const baseTask = {
    task_id:
      "annotation-task-fe13-with-a-deliberately-long-auditable-identifier",
    project_id: "project_fe13_visual",
    region_code: "cn-east-01",
    dataset_id: "dataset-fe13-with-a-very-long-name-that-must-remain-contained",
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
  };
  const queueTasks = [
    baseTask,
    {
      ...baseTask,
      task_id: "annotation-task-fe13-submitted",
      rollout_id: "rollout-fe13-submitted",
      assignee_id: "annotator-fe13",
      current_revision: 2,
      current_submission_id: "submission-fe13-2",
      submitted_revision: 2,
      submitted_by: "annotator-fe13",
      status: "SUBMITTED",
      updated_at: "2026-08-19T09:10:00Z",
    },
    {
      ...baseTask,
      task_id: "annotation-task-fe13-needs-revision",
      rollout_id: "rollout-fe13-needs-revision",
      assignee_id: "actor_fe13_visual",
      current_revision: 3,
      status: "NEEDS_REVISION",
      updated_at: "2026-08-20T10:20:00Z",
    },
    {
      ...baseTask,
      task_id: "annotation-task-fe13-approved",
      rollout_id: "rollout-fe13-approved",
      assignee_id: "annotator-fe13",
      current_revision: 4,
      approved_revision: 4,
      approved_review_id: "review-fe13-approved",
      status: "APPROVED",
      updated_at: "2026-08-21T11:30:00Z",
    },
    {
      ...baseTask,
      task_id: "annotation-task-fe13-rejected",
      rollout_id: "rollout-fe13-rejected",
      assignee_id: "annotator-fe13",
      current_revision: 2,
      status: "REJECTED",
      updated_at: "2026-08-22T12:40:00Z",
    },
  ];
  await page.route(
    "**/api/v1/projects/project_fe13_visual/annotation-tasks**",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(queueTasks),
      });
    },
  );
  await page.goto("/dashboard");
  await page.locator("#main-content").waitFor({ state: "attached" });
  await page.evaluate(async (tasks) => {
    const main = document.getElementById("main-content");
    if (!main) throw new Error("Missing PlatformShell main content");
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (
        url.includes("/api/v1/projects/project_fe13_visual/annotation-tasks")
      ) {
        return Promise.resolve(
          new Response(JSON.stringify(tasks), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return originalFetch(input, init);
    };
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
  }, queueTasks);
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
  await testInfo.attach("axe-p08-report", {
    body: report,
    contentType: "application/json",
  });
  expect(result.violations).toEqual([]);
}

async function expectGeometry(
  page: Page,
  mode: "view" | "annotation" | "tag-review",
): Promise<void> {
  await expect(page.getByLabel("采集条目导航")).toHaveCount(0);
  const boxes = await Promise.all([
    page.getByLabel("相机与同步信号").boundingBox(),
    page.getByLabel("模式工具与发现").boundingBox(),
  ]);
  expect(boxes.every(Boolean)).toBe(true);
  const widths = boxes.map((box) => box?.width ?? 0);
  const total = widths.reduce((sum, width) => sum + width, 0);
  expect(widths[0]! / total).toBeCloseTo(3 / 5, 2);
  const frame = await page
    .locator(`[data-workspace-mode="${mode}"]`)
    .boundingBox();
  const timeline = await page.getByLabel("共享视频时间轴区域").boundingBox();
  const telemetry = await page.getByLabel("诊断动作边界").boundingBox();
  expect(timeline!.width).toBeCloseTo(total + 12, 0);
  expect(timeline!.width).toBeCloseTo(frame!.width, 0);
  expect(timeline!.y).toBeGreaterThanOrEqual(0);
  expect(timeline!.y + timeline!.height).toBeLessThanOrEqual(
    page.viewportSize()!.height + 1,
  );
  expect(
    await page.getByLabel("共享视频时间轴区域").evaluate((element) => {
      const style = getComputedStyle(element);
      return { position: style.position, bottom: style.bottom, top: style.top };
    }),
  ).toEqual({ position: "sticky", bottom: "0px", top: "auto" });
  expect(telemetry!.x).toBeCloseTo(boxes[1]!.x, 0);
  expect(telemetry!.y).toBeGreaterThan(boxes[1]!.y + boxes[1]!.height);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.getByRole("slider", { name: "共享播放位置" })).toHaveCount(
    1,
  );
  await expect(page.getByRole("button", { name: "数据信息" })).toHaveAttribute(
    "aria-expanded",
    "false",
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
        await expect(
          page.getByRole("heading", { name: "多级 Tag" }),
        ).toBeVisible();
        expect(
          await page
            .getByLabel("机器人姿态同步视图")
            .evaluate((element) => getComputedStyle(element).overflowY),
        ).toBe("visible");
        expect(
          await page
            .locator(".viewer-timeline__viewport")
            .evaluate((element) => getComputedStyle(element).overflowY),
        ).toBe("auto");
        await expect(
          page.getByLabel("Tag 名称 *", { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByRole("slider", { name: "共享播放位置" }),
        ).toBeVisible();
        const mediaHeight =
          (await page.getByLabel("相机与同步信号").boundingBox())?.height ?? 0;
        expect(mediaHeight).toBeGreaterThanOrEqual(240);
        const cameraFrames = await page
          .locator(".viewer-media-grid .viewer-panel > svg")
          .evaluateAll((frames) =>
            frames.map((frame) => {
              const box = frame.getBoundingClientRect();
              return {
                top: box.top,
                left: box.left,
                width: box.width,
                ratio: box.width / box.height,
              };
            }),
          );
        expect(cameraFrames).toHaveLength(4);
        expect(cameraFrames[0]!.top).toBe(cameraFrames[1]!.top);
        expect(cameraFrames[2]!.top).toBe(cameraFrames[3]!.top);
        expect(cameraFrames[2]!.top).toBeGreaterThan(cameraFrames[0]!.top);
        expect(cameraFrames[0]!.left).toBe(cameraFrames[2]!.left);
        expect(cameraFrames[1]!.left).toBe(cameraFrames[3]!.left);
        for (const frame of cameraFrames) {
          expect(frame.ratio).toBeCloseTo(16 / 9, 1);
        }
        await expect(
          page.getByRole("article", { name: "关节角变化" }),
        ).toBeVisible();
        await expect(page.getByLabel(/关节角时间序列/u)).toBeVisible();
        await expect(
          page.getByRole("button", { name: "保存修改" }),
        ).toBeVisible();
        await expect(
          page.getByRole("button", { name: "提交审核" }),
        ).toBeVisible();
        await expect(
          page
            .getByLabel("数据清单相机视图")
            .locator(".viewer-panel:not(.viewer-robot-panel)"),
        ).toHaveCount(4);
      } else {
        await expect(page.getByText("六项审核清单")).toHaveCount(0);
        await expect(
          page.getByRole("button", { name: "问题与意见" }),
        ).toHaveAttribute("aria-expanded", "false");
        await expect(page.getByLabel(/补充审核意见/u)).toBeHidden();
        expect(
          (await page
            .getByLabel("Tag 审核操作", { exact: true })
            .boundingBox())!.height,
        ).toBeLessThanOrEqual(48);
        await expect(page.getByLabel(/关节角时间序列/u)).toBeVisible();
        await expect(
          page
            .getByLabel("数据清单相机视图")
            .locator(".viewer-panel:not(.viewer-robot-panel)"),
        ).toHaveCount(4);
        await expect(page.getByLabel("显示视频").locator("option")).toHaveCount(
          6,
        );
      }
      await expect(
        page.getByLabel("数据清单相机视图").locator(".viewer-robot-panel"),
      ).toHaveCount(0);
      await expect(
        page.getByLabel("机器人姿态同步视图").locator(".viewer-robot-panel"),
      ).toHaveCount(1);
      await page.screenshot({
        path: resolve(directory, viewport.file),
        fullPage: true,
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
  await expect(
    page
      .getByLabel("数据清单相机视图")
      .locator(".viewer-panel:not(.viewer-robot-panel)"),
  ).toHaveCount(4);
  await expect(page.getByText("等待摄像头接入")).toHaveCount(4);
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
    page
      .getByLabel("数据清单相机视图")
      .locator(".viewer-panel:not(.viewer-robot-panel)"),
  ).toHaveCount(4);
  await expect(
    page
      .getByLabel("数据清单相机视图")
      .locator(".viewer-panel:not(.viewer-robot-panel)")
      .first(),
  ).toHaveAttribute("aria-label", /主臂相机/u);
  await expect(page.getByText("等待摄像头接入")).toHaveCount(3);
  await page.screenshot({
    path: resolve(directory, "1280x800-one-camera.png"),
    fullPage: false,
  });
});

test("E07 keeps Tag editing visible while watching either camera row", async ({
  page,
}) => {
  const directory = resolve(artifactRoot, "E07");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 4,
    scenario: "reference",
  });

  const timeline = page.getByLabel("共享视频时间轴区域");
  const tagInput = page.getByLabel("Tag 名称");
  await expect(tagInput).toBeInViewport();
  await expect(page.getByRole("button", { name: "创建 Tag" })).toBeInViewport();
  const cameraFrames = page.locator(".viewer-media-grid .viewer-panel > svg");
  const topFrame = await cameraFrames.first().boundingBox();
  const dock = await timeline.boundingBox();
  expect(topFrame!.y + topFrame!.height).toBeLessThanOrEqual(dock!.y);
  await page.evaluate(() =>
    window.scrollTo(0, document.documentElement.scrollHeight),
  );
  await expect(tagInput).toBeInViewport();
  const bottomFrame = await cameraFrames.last().boundingBox();
  const scrolledDock = await timeline.boundingBox();
  expect(bottomFrame!.y).toBeGreaterThanOrEqual(0);
  expect(bottomFrame!.y + bottomFrame!.height).toBeLessThanOrEqual(
    scrolledDock!.y,
  );
  await expect(page.getByText("拖选后自动分级")).toBeVisible();
  await expect(page.getByLabel("父级")).toHaveCount(0);
  await expect(page.getByLabel(/当前 Tag · 共/u)).toHaveCount(0);
  const slider = page.getByRole("slider", { name: "共享播放位置" });
  const sliderBox = await slider.boundingBox();
  expect(sliderBox).not.toBeNull();
  if (sliderBox) {
    await page.mouse.move(
      sliderBox.x + sliderBox.width * 0.18,
      sliderBox.y + 8,
    );
    await page.mouse.down();
    await page.mouse.move(
      sliderBox.x + sliderBox.width * 0.42,
      sliderBox.y + 8,
    );
    await page.mouse.up();
  }
  await expect(
    page.getByRole("button", { name: "拖动整个标注区间" }),
  ).toBeVisible();
  await expect(page.getByText(/自动 L1/u)).toBeVisible();
  await expect(page.getByRole("button", { name: "创建 Tag" })).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: /起点同步播放全部视频和关节数据/u,
    }),
  ).toHaveCount(2);
  await expect(page.getByText(/点击 Tag 从起点同步播放/u)).toBeVisible();
  await expect(timeline.locator(".viewer-timeline__tracks")).not.toContainText(
    "30 Hz 对齐",
  );
  await expect(timeline.locator(".viewer-timeline__tracks")).not.toContainText(
    "数据修订",
  );
  expect(
    (await page.getByRole("heading", { name: "多级 Tag" }).boundingBox())
      ?.height ?? 0,
  ).toBeLessThan(24);
  const scrollBeforeTyping = await page.evaluate(() => window.scrollY);
  await tagInput.fill("拿取物品");
  await tagInput.press("Enter");
  await expect(timeline.locator(".viewer-timeline__tracks")).toContainText(
    "拿取物品",
  );
  await expect(tagInput).toHaveValue("");
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollBeforeTyping);
  const tagSelector = page.getByLabel("编辑已有 Tag");
  const createdTag = page.getByRole("button", { name: /从“拿取物品”起点/u });
  await createdTag.click();
  await expect(tagInput).toHaveValue("拿取物品");
  await expect(createdTag).toHaveAttribute("aria-pressed", "true");
  const cameraClocks = page.locator(".viewer-media-grid time");
  const rangeBeforeDrag = await page
    .getByRole("button", { name: "拖动整个标注区间" })
    .textContent();
  const tagBox = (await createdTag.boundingBox())!;
  await page.mouse.move(
    tagBox.x + tagBox.width / 2,
    tagBox.y + tagBox.height / 2,
  );
  await page.mouse.down();
  const clockBeforeDrag = await cameraClocks.first().textContent();
  await page.mouse.move(
    tagBox.x + tagBox.width / 2 + 35,
    tagBox.y + tagBox.height / 2,
    { steps: 4 },
  );
  await expect(cameraClocks.first()).not.toHaveText(clockBeforeDrag!);
  const liveTimes = await cameraClocks.allTextContents();
  expect(new Set(liveTimes).size).toBe(1);
  await page.mouse.up();
  await expect(
    page.getByRole("button", { name: "拖动整个标注区间" }),
  ).not.toHaveText(rangeBeforeDrag!);
  await expect(
    page.getByRole("button", { name: "应用 Tag 修改" }),
  ).toBeDisabled();
  const endGrip = createdTag.locator('[data-tag-edge="end"]');
  const gripBox = (await endGrip.boundingBox())!;
  await page.mouse.move(
    gripBox.x + gripBox.width / 2,
    gripBox.y + gripBox.height / 2,
  );
  await page.mouse.down();
  const clockBeforeTrim = await cameraClocks.first().textContent();
  await page.mouse.move(gripBox.x + 24, gripBox.y + gripBox.height / 2, {
    steps: 3,
  });
  await expect(cameraClocks.first()).not.toHaveText(clockBeforeTrim!);
  await page.mouse.up();
  await expect(
    page.getByRole("button", { name: "应用 Tag 修改" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "应用 Tag 修改" }),
  ).toBeInViewport();
  await expect(
    page.getByRole("button", { name: "删除 Tag", exact: true }),
  ).toBeInViewport();
  await tagInput.fill("放回物品");
  const endHandle = page.getByRole("button", { name: /^标注结束/u });
  const endBefore = await endHandle.getAttribute("aria-valuetext");
  await endHandle.press("ArrowRight");
  await expect(endHandle).not.toHaveAttribute("aria-valuetext", endBefore!);
  await page.getByRole("button", { name: "应用 Tag 修改" }).click();
  await expect(timeline.locator(".viewer-timeline__tracks")).toContainText(
    "放回物品",
  );
  await expect(timeline.locator(".viewer-timeline__tracks")).not.toContainText(
    "拿取物品",
  );
  await page.screenshot({
    path: resolve(directory, "1280x800-edit-tag.png"),
    fullPage: false,
  });
  const renamedTag = page.getByRole("button", { name: /从“放回物品”起点/u });
  await renamedTag.click({ button: "right" });
  await page.getByRole("menuitem", { name: /删除 Tag/u }).click();
  await expect(timeline.locator(".viewer-timeline__tracks")).not.toContainText(
    "放回物品",
  );
  await expect(tagSelector.getByRole("option")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "创建 Tag" })).toBeInViewport();
  await page.getByRole("button", { name: "撤销删除" }).click();
  await renamedTag.click();
  await tagInput.focus();
  await tagInput.press("Delete");
  await expect(renamedTag).toBeVisible();
  await renamedTag.focus();
  await page.keyboard.press("Delete");
  await expect(renamedTag).toHaveCount(0);
  await expect(page.getByRole("button", { name: "撤销删除" })).toBeInViewport();
  await page.screenshot({
    path: resolve(directory, "1280x800-one-camera-timeline.png"),
    fullPage: false,
  });
});

for (const mode of ["annotation", "tag-review"] as const) {
  test(`keeps the shared layout when switching ${mode} to read-only viewing`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mountFixture(page, { mode, cameraCount: 4 });
    await expectGeometry(page, mode);
    const cameras = page.locator(".viewer-media-grid .viewer-panel");
    const before = await cameras.first().boundingBox();
    await page.getByRole("tab", { name: /查看/ }).click();
    await expectGeometry(page, "view");
    await expect(cameras).toHaveCount(4);
    await expect(page.getByLabel("机器人姿态同步视图")).toBeVisible();
    await expect(
      page.getByRole("article", { name: "关节角变化" }),
    ).toBeVisible();
    await expect(page.getByLabel("Tag 名称")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "问题与意见" })).toHaveCount(
      0,
    );
    const after = await cameras.first().boundingBox();
    expect(after!.width).toBeCloseTo(before!.width, 0);
    expect(after!.height).toBeCloseTo(before!.height, 0);
    await page.getByRole("button", { name: "数据信息" }).click();
    await expect(
      page
        .getByRole("dialog", { name: /数据信息/ })
        .getByRole("heading", { name: "数据查看" }),
    ).toBeVisible();
  });

  test(`allows page scrolling over video, pose and task panels in ${mode}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await mountFixture(page, { mode, cameraCount: 4, scenario: "reference" });

    const surfaces = [
      page.locator(".viewer-media-grid .viewer-panel > svg").first(),
      page.getByLabel("机器人姿态同步视图"),
      page.locator(".viewer-timeline__viewport"),
    ];
    if (mode === "tag-review") {
      await page
        .getByRole("button", { name: "问题与意见", exact: true })
        .click();
      surfaces.push(
        page
          .getByRole("region", { name: "Tag 审核操作" })
          .locator('[class*="inspectorScroll"]'),
      );
    }

    for (const surface of surfaces) {
      await page.evaluate(() => window.scrollTo(0, 0));
      // At an inner panel's end, subsequent wheels must reach the page.
      await surface.evaluate(async (element) => {
        element.scrollTop = element.scrollHeight;
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        );
      });
      await surface.hover();
      const before = await page.evaluate(() => window.scrollY);
      await expect
        .poll(
          async () => {
            // Chromium may finish the inner scroll gesture before handing the
            // next wheel event to the page.
            await page.mouse.wheel(0, 220);
            return page.evaluate(() => window.scrollY);
          },
          { intervals: [250], timeout: 2_000 },
        )
        .toBeGreaterThan(before);
    }
  });
}

test("E07 opens collection data as an overlay without resizing the viewer", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const directory = resolve(artifactRoot, "E07");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 4,
    scenario: "reference",
  });
  const media = page.getByLabel("相机与同步信号");
  const timeline = page.getByLabel("共享视频时间轴区域");
  const before = await Promise.all([
    media.boundingBox(),
    timeline.boundingBox(),
  ]);

  await page.getByRole("button", { name: "数据信息" }).click();
  const drawer = page.getByRole("dialog", { name: /数据信息/u });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "采集条目" })).toBeVisible();
  await expect(drawer.getByText("任务 ID", { exact: true })).toBeVisible();
  await expect(drawer.getByText("数据结构", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Lance", { exact: true })).toBeVisible();
  await expect(drawer.getByText("区间", { exact: true })).toBeVisible();

  const after = await Promise.all([
    media.boundingBox(),
    timeline.boundingBox(),
  ]);
  expect(after).toEqual(before);
  await expect(page.getByRole("slider", { name: "共享播放位置" })).toHaveCount(
    1,
  );
  await page.screenshot({
    path: resolve(directory, "1280x800-data-information-open.png"),
    animations: "disabled",
    fullPage: false,
  });

  await drawer.getByRole("button", { name: /close/i }).click();
  await expect(drawer).not.toBeVisible();
  await expect(page.getByRole("button", { name: "数据信息" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  expect(pageErrors).toEqual([]);
});

test("E07 keeps data information overlay-only on narrow screens", async ({
  page,
}) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 1,
    scenario: "reference",
  });

  await expect(page.getByLabel("采集条目导航")).toHaveCount(0);
  const media = await page.getByLabel("相机与同步信号").boundingBox();
  const timeline = await page.getByLabel("共享视频时间轴区域").boundingBox();
  expect(media).not.toBeNull();
  expect(timeline).not.toBeNull();
  expect(Math.abs((media?.width ?? 0) - (timeline?.width ?? 0))).toBeLessThan(
    1,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    ),
  ).toBeLessThanOrEqual(0);

  await page.getByRole("button", { name: "数据信息" }).click();
  const drawer = page.getByRole("dialog", { name: /数据信息/u });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("任务 ID", { exact: true })).toBeVisible();
  const drawerBox = await drawer.boundingBox();
  expect(drawerBox?.width).toBe(720);
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
  await expect(page.getByRole("button", { name: "提交审核" })).toBeDisabled();
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
      cameraPanels: document.querySelectorAll(
        ".viewer-media-grid .viewer-panel:not(.viewer-robot-panel)",
      ).length,
      robotPanels: document.querySelectorAll(".viewer-robot-panel").length,
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
  expect(profile.cameraPanels).toBe(4);
  expect(profile.robotPanels).toBe(1);
  expect(profile.sharedTimelineCount).toBe(1);
  expect(profile.dispatchMs).toBeLessThan(120);
  expect(profile.longTaskCount).toBeLessThanOrEqual(1);
});

test("E07 exposes the primary workbench controls to the keyboard", async ({
  page,
}, testInfo) => {
  const directory = resolve(artifactRoot, "E07");
  mkdirSync(directory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, {
    mode: "annotation",
    cameraCount: 4,
    scenario: "reference",
  });
  const modeNavigation = page.getByRole("tablist", {
    name: "数据标注工作模式",
  });
  const annotationTab = modeNavigation.getByRole("tab", { name: /^标注/u });
  const viewTab = modeNavigation.getByRole("tab", { name: /^查看/u });
  await annotationTab.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(viewTab).toBeFocused();
  await expect(viewTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(annotationTab).toBeFocused();
  await expect(annotationTab).toHaveAttribute("aria-selected", "true");
  const tagName = page.locator("#manual-tag-label");
  await tagName.focus();
  await expect(tagName).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  const tagPlayback = page
    .getByRole("button", { name: /起点同步播放全部视频和关节数据/u })
    .first();
  await tagPlayback.focus();
  await expect(tagPlayback).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(tagPlayback).toHaveAttribute("aria-current", "time");
  await page.getByRole("button", { name: "暂停" }).click();
  const slider = page.getByRole("slider", { name: "共享播放位置" });
  await slider.focus();
  await expect(slider).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await runAxe(
    page,
    testInfo,
    '[data-p08-visual-host="true"]',
    resolve(directory, "axe-1280x800-workbench.json"),
  );
});

for (const viewport of [
  { width: 2560, height: 1200 },
  { width: 1440, height: 1000 },
  { width: 1280, height: 1000 },
  { width: 768, height: 1200 },
] as const) {
  test(`E07 annotation queue at ${viewport.width}px has contained responsive layout`, async ({
    page,
  }, testInfo) => {
    const directory = resolve(artifactRoot, "E07");
    mkdirSync(directory, { recursive: true });
    await page.setViewportSize(viewport);
    await mountQueueFixture(page);

    const stageNavigation = page.getByRole("navigation", {
      name: "标注任务状态",
    });
    const stageLinks = stageNavigation.getByRole("link");
    await expect(stageLinks).toHaveCount(4);
    const draftLink = stageNavigation.getByRole("link", {
      name: /^待标注，/u,
    });
    await expect(draftLink).toHaveAttribute("aria-current", "page");
    await expect(
      stageNavigation.getByRole("link", { name: /^待审核，/u }),
    ).toHaveAttribute("href", "/annotations/annotate?stage=SUBMITTED");
    await expect(page.getByRole("link", { name: "修订记录" })).toHaveAttribute(
      "href",
      "/annotations/revisions",
    );
    await expect(page.getByText("流程边界")).toHaveCount(0);

    const cardColumns = await stageLinks.evaluateAll(
      (links) =>
        new Set(
          links.map((link) => Math.round(link.getBoundingClientRect().top)),
        ).size,
    );
    const expectedRows = viewport.width >= 1440 ? 1 : 2;
    expect(cardColumns).toBe(expectedRows);

    const queuePanel = page.getByRole("region", {
      name: "待标注",
      exact: true,
    });
    await expect(queuePanel).toBeVisible();
    await expect(page.getByRole("link", { name: /^已拒绝，/u })).toHaveCount(0);

    const search = page.getByLabel("搜索任务、Rollout、Dataset 或数据结构");
    await expect(search).toHaveAttribute("name", "annotation-task-search");
    await expect(search).toHaveAttribute("autocomplete", "off");
    await expect(search).toHaveAttribute("placeholder", "输入关键词…");
    const longId = page.getByTitle(
      "annotation-task-fe13-with-a-deliberately-long-auditable-identifier",
    );
    await expect(longId).toBeVisible();
    expect(
      await longId.evaluate((element) => {
        const style = getComputedStyle(element);
        return style.overflow === "hidden" && style.textOverflow === "ellipsis";
      }),
    ).toBe(true);

    const tableRegion = page.getByRole("region", {
      name: "待标注任务表，可横向滚动",
    });
    expect(
      await tableRegion.evaluate(
        (element) => element.scrollWidth >= element.clientWidth,
      ),
    ).toBe(true);
    const overflow = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
    }));
    expect(overflow.content).toBeLessThanOrEqual(overflow.viewport);

    if (viewport.width === 1280) {
      const revisionLink = page.getByRole("link", { name: "修订记录" });
      await revisionLink.focus();
      await expect(revisionLink).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "刷新" })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(draftLink).toBeFocused();
      expect(
        await draftLink.evaluate((element) => {
          const style = getComputedStyle(element);
          return (
            style.outlineStyle !== "none" &&
            Number.parseFloat(style.outlineWidth) >= 2
          );
        }),
      ).toBe(true);
      await page.keyboard.press("Tab");
      await expect(
        stageNavigation.getByRole("link", { name: /^待审核，/u }),
      ).toBeFocused();
    }

    await runAxe(
      page,
      testInfo,
      '[data-fe13-annotation-queue-host="true"]',
      resolve(directory, `axe-queue-${viewport.width}.json`),
    );
    await page.evaluate(() =>
      (document.activeElement as HTMLElement | null)?.blur(),
    );
    await page.screenshot({
      path: resolve(
        directory,
        `annotation-queue-${viewport.width}x${viewport.height}.png`,
      ),
      animations: "disabled",
      fullPage: true,
    });
  });
}
