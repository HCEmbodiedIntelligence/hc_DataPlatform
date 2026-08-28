import { existsSync, mkdirSync, writeFileSync } from "node:fs";
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
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe16"
    ? "../artifacts/visual/e01-e10/FE16-final/fixture/E09"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
      ? "../artifacts/visual/e01-e10/FE14-final/fixture/E09"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe15"
        ? "../artifacts/visual/e01-e10/FE15-fixes/E09"
        : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
          ? "../artifacts/visual/e01-e10/FE12-final/E09"
          : "../artifacts/visual/e01-e10/E09",
);
const repairArtifactDirectory = resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe16"
    ? "../artifacts/visual/e01-e10/FE16-final/fixture"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
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
    readonly scenario: "reference" | "users" | "forbidden" | "empty";
    readonly tab?: "users" | "membership-requests" | "capability-requests";
  },
) {
  await page.emulateMedia({ reducedMotion: "reduce" });
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
          readonly scenario: "reference" | "users" | "forbidden" | "empty";
          readonly tab?:
            | "users"
            | "membership-requests"
            | "capability-requests";
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

test("E09 platform user management stays usable across desktop and mobile", async ({
  page,
}, testInfo) => {
  mkdirSync(artifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await mountFixture(page, { scenario: "users", tab: "users" });
  await expect(page.getByRole("tab", { name: "用户管理" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByRole("region", { name: "平台用户管理" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "创建用户" })).toBeEnabled();
  await expect(page.getByText("h***@example.cn")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const axe = await runAxe(page);
  if (axe) {
    const report = JSON.stringify({ violations: axe.violations }, null, 2);
    writeFileSync(
      resolve(artifactDirectory, "axe-users-1440x900.json"),
      report,
    );
    await testInfo.attach("axe-users", {
      body: report,
      contentType: "application/json",
    });
    expect(axe.violations).toEqual([]);
  }
  await page.screenshot({
    path: resolve(artifactDirectory, "users-1440x900.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalOverflow(page);
  await expect(page.getByRole("button", { name: "创建用户" })).toBeVisible();
  await page.screenshot({
    path: resolve(artifactDirectory, "users-375x812.png"),
    animations: "disabled",
    fullPage: false,
  });
});

async function expectFullWidthList(page: Page) {
  const [pageBox, listBox] = await Promise.all([
    page.locator("[data-e09-page]").boundingBox(),
    page.locator("[data-e09-main]").boundingBox(),
  ]);
  expect(pageBox).not.toBeNull();
  expect(listBox).not.toBeNull();
  expect((listBox?.width ?? 0) / (pageBox?.width ?? 1)).toBeGreaterThan(0.98);
}

async function expectDrawerOverlay(page: Page, viewportWidth: number) {
  const drawer = await page.locator("[data-e09-drawer]").boundingBox();
  expect(drawer).not.toBeNull();
  expect(drawer?.width ?? 0).toBeLessThanOrEqual(viewportWidth);
  if (viewportWidth <= 375) {
    expect(Math.abs((drawer?.width ?? 0) - viewportWidth)).toBeLessThanOrEqual(
      1,
    );
  } else {
    expect(drawer?.width ?? 0).toBeGreaterThanOrEqual(520);
  }
  expect((drawer?.y ?? 0) + (drawer?.height ?? 0)).toBeLessThanOrEqual(
    await page.evaluate(() => window.innerHeight + 1),
  );
  await expectNoHorizontalOverflow(page);
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
  const axePath =
    process.env.AXE_CORE_PATH ??
    resolve(process.cwd(), "node_modules/axe-core/axe.min.js");
  if (!existsSync(axePath)) return null;
  await page.addScriptTag({ path: axePath });
  return page.evaluate(async () => {
    const axe = (
      window as Window & {
        axe?: {
          run(
            root:
              | Element
              | Document
              | { readonly include: readonly (readonly string[])[] },
          ): Promise<AxeResult>;
        };
      }
    ).axe;
    const fixture = document.querySelector("[data-p18-visual-fixture]");
    if (!axe || !fixture) throw new Error("Missing axe-core or E09 fixture");
    const include: string[][] = [["[data-p18-visual-fixture]"]];
    if (document.querySelector("[data-e09-drawer]"))
      include.push(["[data-e09-drawer]"]);
    return axe.run({ include });
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
    await expect(page.locator("[data-e09-drawer]")).toHaveCount(0);
    await expect(page.getByText(/注册账户不进入审批队列/u)).toHaveCount(0);
    await expectFullWidthList(page);
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: resolve(artifactDirectory, viewport.file),
      animations: "disabled",
      fullPage: false,
    });

    const selectedRequest = page.getByRole("button", {
      name: "查看 contractor-li.ming-017 的申请",
    });
    await selectedRequest.click();
    await expect(page.locator("[data-e09-drawer]")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toBeVisible();
    await expectDrawerOverlay(page, viewport.width);

    const close = page.getByRole("button", { name: "关闭申请详情" });
    await close.focus();
    await page.keyboard.press("Escape");
    await expect
      .poll(() =>
        page.evaluate(() => ({
          ariaLabel: document.activeElement?.getAttribute("aria-label"),
          tag: document.activeElement?.tagName,
          text: document.activeElement?.textContent,
        })),
      )
      .toEqual({
        ariaLabel: "查看 contractor-li.ming-017 的申请",
        tag: "BUTTON",
        text: "查看",
      });

    await selectedRequest.click();
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
      path: resolve(
        artifactDirectory,
        viewport.file.replace(".png", "-drawer.png"),
      ),
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
  await expect(page.locator("[data-e09-drawer]")).toHaveCount(0);
  await expectFullWidthList(page);
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: resolve(repairArtifactDirectory, "375x812-list.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page
    .getByRole("button", { name: "查看 contractor-li.ming-017 的申请" })
    .click();
  await expect(page.locator("[data-e09-drawer]")).toHaveCount(1);
  await expectDrawerOverlay(page, 375);
  await page.getByRole("button", { name: "批准申请" }).click();
  await expect(
    page.getByRole("button", { name: "提交批准申请" }),
  ).toBeVisible();
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
  const axe = await runAxe(page);
  if (axe) expect(axe.violations).toEqual([]);
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
  const decidedRow = page.getByRole("button", {
    name: "查看 contractor-li.ming-017 的申请",
  });
  await expect(decidedRow).toBeVisible();
  await decidedRow.click();
  await page.getByRole("button", { name: "批准申请" }).click();
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
