import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const artifactsDirectory = path.resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture/E02"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
      ? "../artifacts/visual/e01-e10/FE12-final/E02"
      : "../artifacts/visual/e01-e10/E02",
);

test.beforeAll(() => mkdirSync(artifactsDirectory, { recursive: true }));

const viewports = [
  {
    name: "1440x900",
    width: 1440,
    height: 900,
    navigationWidth: 218,
    gutter: 24,
  },
  {
    name: "1280x800",
    width: 1280,
    height: 800,
    navigationWidth: 64,
    gutter: 16,
  },
] as const;

for (const viewport of viewports) {
  test(`E02 loading shell ${viewport.name}`, async ({ page }) => {
    const apiRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) {
        apiRequests.push(request.url());
      }
    });
    await page.addInitScript(() => {
      const metrics = window as typeof window & { __e02LayoutShift?: number };
      metrics.__e02LayoutShift = 0;
      new PerformanceObserver((list) => {
        for (const rawEntry of list.getEntries()) {
          const entry = rawEntry as PerformanceEntry & {
            hadRecentInput: boolean;
            value: number;
          };
          if (!entry.hadRecentInput) {
            metrics.__e02LayoutShift =
              (metrics.__e02LayoutShift ?? 0) + entry.value;
          }
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await page.goto("/dashboard", { waitUntil: "networkidle" });
    await expect(page.getByLabel("工作台加载中")).toBeVisible();
    await expect(page.getByRole("img", { name: "杭叉集团" })).toHaveAttribute(
      "src",
      /hangcha-logo/u,
    );
    await expect(
      page.getByRole("button", { name: "全局搜索尚未开放" }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "通知尚未开放" }),
    ).toBeDisabled();

    const geometry = await page.evaluate(() => {
      const header = document.querySelector("header.ant-layout-header");
      const sider = document.querySelector("aside.ant-layout-sider");
      const main = document.querySelector("main");
      const logo =
        document.querySelector<HTMLImageElement>('img[alt="杭叉集团"]');
      if (
        !(header instanceof HTMLElement) ||
        !(sider instanceof HTMLElement) ||
        !(main instanceof HTMLElement) ||
        logo === null
      ) {
        throw new Error("E02 shell geometry nodes are missing");
      }
      const mainStyle = getComputedStyle(main);
      const metrics = window as typeof window & { __e02LayoutShift?: number };
      return {
        headerHeight: header.getBoundingClientRect().height,
        navigationWidth: sider.getBoundingClientRect().width,
        gutter: Number.parseFloat(mainStyle.paddingInlineStart),
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        logoWidth: logo.getBoundingClientRect().width,
        logoHeight: logo.getBoundingClientRect().height,
        logoComplete: logo.complete,
        layoutShift: metrics.__e02LayoutShift ?? 0,
      };
    });

    expect(geometry.headerHeight).toBe(68);
    expect(geometry.navigationWidth).toBe(viewport.navigationWidth);
    expect(geometry.gutter).toBe(viewport.gutter);
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
    expect(geometry.logoWidth).toBeGreaterThan(0);
    expect(geometry.logoHeight).toBeGreaterThan(0);
    expect(geometry.logoComplete).toBe(true);
    expect(geometry.layoutShift).toBeLessThanOrEqual(0.01);
    expect(apiRequests).toEqual([]);

    for (const accessibleName of ["当前项目", "当前区域"]) {
      const scopeGeometry = await page
        .getByRole("combobox", { name: accessibleName })
        .evaluate((element) => {
          const field = element.closest("label");
          const icon = field?.querySelector("svg");
          const value = field?.querySelector(".ant-select-content");
          const valueText = value?.firstChild;
          if (
            !(icon instanceof SVGElement) ||
            !(value instanceof HTMLElement) ||
            valueText?.nodeType !== Node.TEXT_NODE
          ) {
            throw new Error("Scope selector geometry nodes are missing");
          }
          const valueRange = document.createRange();
          valueRange.selectNodeContents(valueText);
          return {
            iconRight: icon.getBoundingClientRect().right,
            valueLeft: valueRange.getBoundingClientRect().left,
          };
        });
      expect(scopeGeometry.valueLeft).toBeGreaterThanOrEqual(
        scopeGeometry.iconRight + 4,
      );
    }

    if (viewport.width === 1440) {
      await expect(page.getByText("采集与接收", { exact: true })).toBeVisible();
      await expect(page.getByRole("link", { name: "采集任务" })).toBeVisible();
      await expect(page.getByRole("link", { name: "数据上传" })).toBeVisible();
      await expect(page.getByRole("link", { name: "数据标注" })).toBeVisible();
      await expect(page.getByText("数据清洗", { exact: true })).toHaveCount(0);
    }

    await page.screenshot({
      path: path.join(artifactsDirectory, `${viewport.name}-loading.png`),
      animations: "disabled",
      fullPage: false,
    });
  });
}

test("E02 keyboard order and focus visibility", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/dashboard", { waitUntil: "networkidle" });

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "跳到主要内容" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();

  const dashboardLink = page.getByRole("link", { name: "工作台", exact: true });
  await dashboardLink.focus();
  const focusStyle = await dashboardLink.evaluate((element) => {
    return getComputedStyle(element).outlineStyle;
  });
  expect(focusStyle).not.toBe("none");

  await page.getByRole("button", { name: "账户菜单" }).click();
  await expect(page.getByText("账户设置（尚未开放）")).toBeVisible();
  await expect(page.getByText("退出登录（尚未接入）")).toBeVisible();
});
