import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

interface AxeResult {
  readonly violations: readonly {
    readonly id: string;
    readonly impact: string | null;
    readonly nodes: readonly {
      readonly target: readonly string[];
      readonly failureSummary?: string;
    }[];
  }[];
}

const artifactDirectory = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture/E09"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe15"
      ? "../artifacts/visual/e01-e10/FE15-fixes/E09"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
        ? "../artifacts/visual/e01-e10/FE12-final/E09"
        : "../artifacts/visual/e01-e10/E09",
);
const repairArtifactDirectory = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe15"
      ? "../artifacts/visual/e01-e10/FE15-fixes/E09"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
        ? "../artifacts/visual/e01-e10/FE12-final"
        : "../artifacts/visual/e01-e10/FE11-repair",
);

async function mountFixture(
  page: Page,
  options: {
    readonly scenario: "reference" | "forbidden" | "empty";
    readonly tab?: "membership-requests" | "capability-requests";
  },
) {
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
    host.dataset.e09VisualHost = "true";
    main.append(host);
    type VisualModule = {
      mountAccessApprovalVisualFixture(
        element: HTMLElement,
        value: {
          readonly scenario: "reference" | "forbidden" | "empty";
          readonly tab?: "membership-requests" | "capability-requests";
        },
      ): void;
    };
    const load = new Function(
      'return import("/src/pages/p18-access/testing/AccessApprovalVisualFixture.tsx")',
    ) as () => Promise<VisualModule>;
    const module = await load();
    module.mountAccessApprovalVisualFixture(host, fixtureOptions);
  }, options);
  await expect(
    page.getByRole("heading", { level: 1, name: "账户与权限" }),
  ).toBeVisible();
}

async function expectGeometry(page: Page) {
  const [main, drawer] = await Promise.all([
    page.locator("[data-e09-main]").boundingBox(),
    page.locator("[data-e09-drawer]").boundingBox(),
  ]);
  expect(main).not.toBeNull();
  expect(drawer).not.toBeNull();
  const total = (main?.width ?? 0) + (drawer?.width ?? 0);
  expect((drawer?.width ?? 0) / total).toBeGreaterThan(0.34);
  expect((drawer?.width ?? 0) / total).toBeLessThan(0.47);
  expect((drawer?.y ?? 0) + (drawer?.height ?? 0)).toBeLessThanOrEqual(
    await page.evaluate(() => window.innerHeight + 1),
  );
  const horizontalGeometry = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    offenders: [...document.querySelectorAll("body *")]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return (
          box.width > 0 && (box.left < -1 || box.right > window.innerWidth + 1)
        );
      })
      .slice(0, 12)
      .map((element) => ({
        node: element.tagName,
        className: element.className,
        box: element.getBoundingClientRect().toJSON(),
      })),
  }));
  expect(
    horizontalGeometry.overflow,
    JSON.stringify(horizontalGeometry.offenders, null, 2),
  ).toBeLessThanOrEqual(0);
}

async function expectNoHorizontalOverflow(page: Page) {
  const horizontalGeometry = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    offenders: [...document.querySelectorAll("body *")]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return (
          box.width > 0 && (box.left < -1 || box.right > window.innerWidth + 1)
        );
      })
      .slice(0, 12)
      .map((element) => ({
        node: element.tagName,
        className: element.className,
        box: element.getBoundingClientRect().toJSON(),
      })),
  }));
  expect(
    horizontalGeometry.overflow,
    JSON.stringify(horizontalGeometry.offenders, null, 2),
  ).toBeLessThanOrEqual(0);
}

async function runAxe(page: Page): Promise<AxeResult | null> {
  const axePath = process.env.AXE_CORE_PATH;
  if (!axePath) return null;
  await page.addScriptTag({ path: axePath });
  return page.evaluate(async () => {
    const axe = (
      window as Window & { axe?: { run(root: Element): Promise<AxeResult> } }
    ).axe;
    const fixture = document.querySelector("[data-p18-visual-fixture]");
    if (!axe || !fixture) throw new Error("Missing axe-core or E09 fixture");
    return axe.run(fixture);
  });
}

for (const viewport of [
  { width: 1440, height: 900, file: "1440x900.png" },
  { width: 1280, height: 800, file: "1280x800.png" },
] as const) {
  test(`E09 reference layout at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    mkdirSync(artifactDirectory, { recursive: true });
    await page.setViewportSize(viewport);
    await mountFixture(page, { scenario: "reference" });
    await expect(
      page.getByRole("tab", { name: /项目加入申请/u }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("dialog", { name: "审批申请" })).toBeVisible();
    await expect(page.getByText("注册账户不进入审批队列。")).toBeVisible();
    await expectGeometry(page);

    const close = page.getByRole("button", { name: "关闭审批抽屉" });
    await close.focus();
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-selected]")).toBeFocused();

    await page.locator("[data-selected]").click();
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
      animations: "disabled",
      fullPage: false,
    });
  });
}

test("E09 keeps a scoped 403 visible without fallback data", async ({
  page,
}) => {
  mkdirSync(artifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountFixture(page, { scenario: "forbidden" });
  await expect(
    page.getByText("当前身份无权读取该项目的访问申请。"),
  ).toBeVisible();
  await expect(page.getByText("visual-access-403")).toBeVisible();
  await expect(page.locator("[data-e09-drawer]")).toHaveCount(0);
  await page.screenshot({
    path: resolve(artifactDirectory, "1280x800-403.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("E09 keeps lightweight approval usable at 375x812", async ({ page }) => {
  mkdirSync(repairArtifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await mountFixture(page, { scenario: "reference" });
  await expect(page.getByRole("dialog", { name: "审批申请" })).toBeVisible();
  await expect(page.getByRole("button", { name: "提交批准" })).toBeVisible();
  const horizontalGeometry = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    offenders: [...document.querySelectorAll("body *")]
      .filter((element) => {
        const box = element.getBoundingClientRect();
        return (
          box.width > 0 && (box.left < -1 || box.right > window.innerWidth + 1)
        );
      })
      .slice(0, 12)
      .map((element) => ({
        node: element.tagName,
        className: element.className,
        box: element.getBoundingClientRect().toJSON(),
      })),
  }));
  expect(
    horizontalGeometry.overflow,
    JSON.stringify(horizontalGeometry.offenders, null, 2),
  ).toBeLessThanOrEqual(0);
  await page.screenshot({
    path: resolve(repairArtifactDirectory, "375x812-light-approval.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("E09 keeps server-confirmed success after the decided row and drawer disappear", async ({
  page,
}, testInfo) => {
  mkdirSync(artifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountFixture(page, { scenario: "reference" });
  const decidedRow = page
    .locator("button")
    .filter({ has: page.getByTitle("contractor-li.ming-017") });
  await expect(decidedRow).toBeVisible();
  await page.getByRole("button", { name: "提交批准申请" }).click();

  await expect(decidedRow).toHaveCount(0);
  await expect(page.locator("[data-e09-drawer]")).toHaveCount(0);
  const status = page.getByRole("status");
  await expect(status).toContainText("批准申请已由服务端确认。");
  await expect(status).toHaveAttribute("aria-live", "polite");
  await expect(
    page.getByRole("button", { name: "关闭成功提示" }),
  ).toBeFocused();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: resolve(artifactDirectory, "1440x900-success-row-removed.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  await expectNoHorizontalOverflow(page);
  const axe = await runAxe(page);
  if (axe) {
    const report = JSON.stringify({ violations: axe.violations }, null, 2);
    writeFileSync(
      resolve(artifactDirectory, "axe-1280x800-success-row-removed.json"),
      report,
    );
    await testInfo.attach("axe-success-row-removed", {
      body: report,
      contentType: "application/json",
    });
    expect(axe.violations).toEqual([]);
  }
  await page.screenshot({
    path: resolve(artifactDirectory, "1280x800-success-row-removed.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalOverflow(page);
  await expect(status).toBeVisible();
  await page.screenshot({
    path: resolve(artifactDirectory, "375x812-success-row-removed.png"),
    animations: "disabled",
    fullPage: false,
  });
});
