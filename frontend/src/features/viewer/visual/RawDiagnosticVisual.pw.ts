import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

interface AxeResult {
  readonly violations: readonly {
    readonly id: string;
    readonly impact: string | null;
    readonly description: string;
    readonly nodes: readonly {
      readonly target: readonly string[];
      readonly failureSummary?: string;
    }[];
  }[];
}

const artifactDirectory = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe16"
    ? "../artifacts/visual/e01-e10/FE16-final/fixture/E06"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
      ? "../artifacts/visual/e01-e10/FE14-final/fixture/E06"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
        ? "../artifacts/visual/e01-e10/FE12-final/E06"
        : "../artifacts/visual/e01-e10/E06",
);

async function mountFixture(
  page: Page,
  options: {
    readonly cameraCount: number;
    readonly scenario: "reference" | "missing-slow" | "empty";
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
    host.dataset.e06VisualFixture = "true";
    main.append(host);
    type VisualModule = {
      mountRawDiagnosticVisualFixture(
        element: HTMLElement,
        value: {
          readonly cameraCount: number;
          readonly scenario: "reference" | "missing-slow" | "empty";
        },
      ): void;
    };
    const load = new Function(
      'return import("/src/features/viewer/testing/RawDiagnosticVisualFixture.tsx")',
    ) as () => Promise<VisualModule>;
    const visualModule = await load();
    visualModule.mountRawDiagnosticVisualFixture(host, fixtureOptions);
  }, options);
  await expect(
    page.getByRole("heading", { level: 1, name: "Raw 诊断" }),
  ).toBeVisible();
}

async function expectWorkbenchGeometry(page: Page): Promise<void> {
  const regions = await Promise.all([
    page.getByLabel("采集条目导航").boundingBox(),
    page.getByLabel("相机与同步信号").boundingBox(),
    page.getByLabel("模式工具与发现").boundingBox(),
  ]);
  expect(regions.every(Boolean)).toBe(true);
  const widths = regions.map((region) => region?.width ?? 0);
  const [navigationWidth = 0, mediaWidth = 0, inspectorWidth = 0] = widths;
  const total = widths.reduce((sum, width) => sum + width, 0);
  expect(navigationWidth / total).toBeGreaterThan(0.15);
  expect(navigationWidth / total).toBeLessThan(0.21);
  expect(mediaWidth / total).toBeGreaterThan(0.52);
  expect(mediaWidth / total).toBeLessThan(0.6);
  expect(inspectorWidth / total).toBeGreaterThan(0.23);
  expect(inspectorWidth / total).toBeLessThan(0.29);
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(horizontalOverflow).toBeLessThanOrEqual(0);
}

async function runAxe(page: Page): Promise<AxeResult | null> {
  const axePath = process.env.AXE_CORE_PATH;
  if (!axePath) return null;
  await page.addScriptTag({ path: axePath });
  return page.evaluate(async () => {
    const axe = (
      window as Window & { axe?: { run(root: Element): Promise<AxeResult> } }
    ).axe;
    if (!axe) throw new Error("axe-core did not load");
    const fixture = document.querySelector("[data-e06-visual-fixture]");
    if (!fixture) throw new Error("Missing E06 visual fixture");
    return axe.run(fixture);
  });
}

for (const viewport of [
  { width: 1440, height: 900, file: "1440x900.png" },
  { width: 1280, height: 800, file: "1280x800.png" },
] as const) {
  test(`E06 reference layout at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    mkdirSync(artifactDirectory, { recursive: true });
    await page.setViewportSize(viewport);
    await mountFixture(page, { cameraCount: 4, scenario: "reference" });
    await expect(
      page.getByText("异常数据保留在 Raw，不进入 Lance"),
    ).toBeVisible();
    await expect(
      page.getByRole("slider", { name: "共享播放位置" }),
    ).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: /PASS|通过|放行/u }),
    ).toHaveCount(0);
    await expectWorkbenchGeometry(page);
    // CameraClockBadge subscribes in an effect.  Wait for its fixed fixture
    // value before the screenshot so baseline pixels do not depend on effect
    // scheduling.
    await expect(page.getByText("7.632 s", { exact: true })).toHaveCount(4);
    const axe = await runAxe(page);
    if (axe) {
      writeFileSync(
        resolve(
          artifactDirectory,
          `axe-${viewport.width}x${viewport.height}.json`,
        ),
        JSON.stringify({ violations: axe.violations }, null, 2),
      );
      await testInfo.attach(`axe-${viewport.width}`, {
        body: JSON.stringify(axe, null, 2),
        contentType: "application/json",
      });
      expect(axe.violations).toEqual([]);
    }
    await page.screenshot({
      path: resolve(artifactDirectory, viewport.file),
      fullPage: false,
    });
  });
}

test("E06 missing and slow streams remain local at 1280x800", async ({
  page,
}) => {
  mkdirSync(artifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, { cameraCount: 4, scenario: "missing-slow" });
  await expect(page.getByText("流缺失", { exact: true })).toBeVisible();
  await expect(
    page.getByText("局部缺流或缺帧不会清空其他相机与信号轨道。"),
  ).toBeVisible();
  await expect(page.getByText("812 帧 · 30.00 fps").first()).toBeVisible();
  await page.screenshot({
    path: resolve(artifactDirectory, "1280x800-missing-slow.png"),
    fullPage: false,
  });
});

test("8-camera clock interaction stays within the local render budget", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, { cameraCount: 8, scenario: "reference" });
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
    };
  });
  await testInfo.attach("8-camera-performance-profile", {
    body: JSON.stringify(profile, null, 2),
    contentType: "application/json",
  });
  mkdirSync(artifactDirectory, { recursive: true });
  writeFileSync(
    resolve(artifactDirectory, "8-camera-performance-profile.json"),
    JSON.stringify(profile, null, 2),
  );
  expect(profile.cameraPanels).toBe(8);
  expect(profile.dispatchMs).toBeLessThan(120);
  expect(profile.longTaskCount).toBeLessThanOrEqual(1);
});
