import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

interface AxeResult {
  violations: readonly {
    id: string;
    impact?: string | null;
    nodes: readonly unknown[];
  }[];
}

const artifactRoot = path.resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture/E10"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe13"
      ? "../artifacts/visual/e01-e10/FE13-fixes/E10"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
        ? "../artifacts/visual/e01-e10/FE12-final/E10"
        : "../artifacts/visual/e01-e10/E10",
);
const happyScenario = "mockScenario=management%3Ahappy";
const partialScenario = "mockScenario=storage-overview%3Afatal-error";

async function expectNoPageOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(overflow.content).toBeLessThanOrEqual(overflow.viewport);
}

async function expectGovernanceLayout(page: Page) {
  await expect(page.getByRole("heading", { name: "容量管理" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "生命周期策略" }),
  ).toBeVisible();
  const capacity = page.locator(
    'section[aria-labelledby="capacity-pane-title"]',
  );
  const lifecycle = page.locator(
    'section[aria-labelledby="lifecycle-pane-title"]',
  );
  const [capacityBox, lifecycleBox] = await Promise.all([
    capacity.boundingBox(),
    lifecycle.boundingBox(),
  ]);
  expect(capacityBox).not.toBeNull();
  expect(lifecycleBox).not.toBeNull();
  const ratio = capacityBox!.width / (capacityBox!.width + lifecycleBox!.width);
  expect(ratio).toBeGreaterThanOrEqual(0.45);
  expect(ratio).toBeLessThanOrEqual(0.47);
}

async function runAxe(
  page: Page,
  testInfo: TestInfo,
  viewportName: string,
  rootSelector = '[data-page-id="P12-P13"]',
) {
  const axePath = process.env.AXE_CORE_PATH;
  if (!axePath) {
    if (
      process.env.HC_REAL_API_E2E_RUN_OWNER === "fe13" ||
      process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    )
      throw new Error("AXE_CORE_PATH is required for FE13/FE14 visual checks");
    return;
  }
  await page.addScriptTag({ path: axePath });
  const result = await page.evaluate(
    async ({ selector }) => {
      const axe = (
        window as Window & { axe?: { run(root: Element): Promise<AxeResult> } }
      ).axe;
      const root = document.querySelector(selector);
      if (!axe || !root) throw new Error("axe-core or E10 root did not load");
      return axe.run(root);
    },
    { selector: rootSelector },
  );
  const report = JSON.stringify({ violations: result.violations }, null, 2);
  writeFileSync(path.join(artifactRoot, `axe-${viewportName}.json`), report);
  await testInfo.attach(`axe-${viewportName}`, {
    body: report,
    contentType: "application/json",
  });
  expect(result.violations).toEqual([]);
}

async function mountObjectDrawerFixture(page: Page) {
  await page.goto(`/storage/overview?${happyScenario}`, {
    waitUntil: "networkidle",
  });
  await page.locator("#main-content").waitFor({ state: "attached" });
  await page.evaluate(async () => {
    const main = document.getElementById("main-content");
    if (!main) throw new Error("Missing PlatformShell main content");
    const host = document.createElement("div");
    host.dataset.fe13StorageDrawerHost = "true";
    main.prepend(host);
    type DrawerVisualModule = {
      mountStorageObjectDrawerVisualFixture(element: HTMLElement): void;
    };
    const load = new Function(
      'return import("/src/pages/p12-storage-overview/testing/StorageObjectDrawerVisualFixture.tsx")',
    ) as () => Promise<DrawerVisualModule>;
    (await load()).mountStorageObjectDrawerVisualFixture(host);
  });
  await expect(
    page.getByRole("button", { name: "查看超长对象详情" }),
  ).toBeVisible();
}

test.beforeAll(() => {
  mkdirSync(artifactRoot, { recursive: true });
});

for (const viewport of [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
] as const) {
  test(`E10 combined governance ${viewport.name}`, async ({
    page,
  }, testInfo) => {
    const storageRequests: string[] = [];
    page.on("request", (request) => {
      const pathname = new URL(request.url()).pathname;
      if (pathname.includes("/storage/")) storageRequests.push(pathname);
    });
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await page.goto(`/storage/overview?${happyScenario}`, {
      waitUntil: "networkidle",
    });

    await expectGovernanceLayout(page);
    await expect(page.getByText("对账一致")).toBeVisible();
    await expect(
      page.getByRole("figure", { name: "增长趋势" }),
    ).toHaveAccessibleDescription(/不能计算增长率/u);
    await expect(page.getByText("Raw", { exact: true }).first()).toBeVisible();
    await expect(
      page.getByText("Manifest", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("已发布 Manifest", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /模拟|执行/u })).toHaveCount(
      0,
    );
    await expectNoPageOverflow(page);

    expect(
      storageRequests.some((request) => request.endsWith("/storage/capacity")),
    ).toBe(true);
    expect(
      storageRequests.some((request) =>
        request.endsWith("/storage/lifecycle-policies"),
      ),
    ).toBe(true);
    expect(
      storageRequests.some((request) =>
        request.endsWith("/storage/lifecycle-audit"),
      ),
    ).toBe(true);
    expect(
      storageRequests.every(
        (request) => !/simulat|impact-preview|dry-run|cost/u.test(request),
      ),
    ).toBe(true);

    const search = page.getByPlaceholder("搜索策略名称或 ID");
    await search.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(search).toBeFocused();
    expect(
      await search.evaluate((element) => {
        const wrapper = element.closest(".ant-input-affix-wrapper");
        return wrapper instanceof HTMLElement
          ? getComputedStyle(wrapper).boxShadow
          : "none";
      }),
    ).not.toBe("none");

    await runAxe(page, testInfo, viewport.name);
    await page.evaluate(() => {
      (document.activeElement as HTMLElement | null)?.blur();
      window.scrollTo({ top: 0 });
    });
    await page.screenshot({
      path: path.join(artifactRoot, `${viewport.name}.png`),
      animations: "disabled",
      fullPage: false,
    });
  });
}

test("E10 keeps lifecycle protection visible when the capacity region fails", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/storage/overview?${partialScenario}`, {
    waitUntil: "networkidle",
  });

  await expect(page.getByText("此区域暂时不可用")).toBeVisible();
  await expect(page.getByText("保护状态")).toBeVisible();
  await expect(page.getByText("Raw", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /模拟|执行/u })).toHaveCount(0);
  await expectNoPageOverflow(page);
  await page.screenshot({
    path: path.join(artifactRoot, "1440x900-capacity-error.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("P13 route reuses the same combined page without a simulate request", async ({
  page,
}) => {
  const storageRequests: string[] = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.includes("/storage/")) storageRequests.push(pathname);
  });
  await page.goto(`/storage/lifecycle?${happyScenario}`, {
    waitUntil: "networkidle",
  });

  await expectGovernanceLayout(page);
  expect(
    storageRequests.every(
      (request) => !/simulat|impact-preview|dry-run|cost/u.test(request),
    ),
  ).toBe(true);
});

test("E10 object drawer manages desktop focus, Escape, long text and axe", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mountObjectDrawerFixture(page);
  const trigger = page.getByRole("button", { name: "查看超长对象详情" });
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: "对象详情" });
  await expect(dialog).toBeVisible();
  expect(
    await dialog.evaluate((element) => {
      const active = document.activeElement;
      return (
        element.contains(active) ||
        (active instanceof HTMLElement &&
          active.classList.contains("ant-drawer") &&
          active.contains(element))
      );
    }),
  ).toBe(true);
  await expect(dialog.locator("[autofocus]")).toHaveCount(0);
  const overflow = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth - window.innerWidth,
    dialog: (() => {
      const element = document.querySelector<HTMLElement>('[role="dialog"]');
      return element ? element.scrollWidth - element.clientWidth : 1;
    })(),
  }));
  expect(overflow.page).toBeLessThanOrEqual(0);
  expect(overflow.dialog).toBeLessThanOrEqual(0);
  await runAxe(page, testInfo, "1280x800-object-drawer", '[role="dialog"]');
  await page.screenshot({
    path: path.join(artifactRoot, "1280x800-object-drawer.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("E10 object drawer avoids mobile autofocus and soft-keyboard targets", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mountObjectDrawerFixture(page);
  const trigger = page.getByRole("button", { name: "查看超长对象详情" });
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: "对象详情" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[autofocus]")).toHaveCount(0);
  expect(
    await page.evaluate(() => {
      const active = document.activeElement;
      return active?.tagName !== "INPUT" && active?.tagName !== "TEXTAREA";
    }),
  ).toBe(true);
  await expectNoPageOverflow(page);
  await runAxe(page, testInfo, "375x812-object-drawer", '[role="dialog"]');
  await page.screenshot({
    path: path.join(artifactRoot, "375x812-object-drawer.png"),
    animations: "disabled",
    fullPage: false,
  });

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
