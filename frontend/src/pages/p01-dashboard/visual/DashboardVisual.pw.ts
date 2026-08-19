import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const artifactDirectory = path.resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture/E03"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
      ? "../artifacts/visual/e01-e10/FE12-final/E03"
      : "../artifacts/visual/e01-e10/E03",
);
const repairArtifactDirectory = path.resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "../artifacts/visual/e01-e10/FE14-final/fixture"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
      ? "../artifacts/visual/e01-e10/FE12-final"
      : "../artifacts/visual/e01-e10/FE11-repair",
);

test.beforeAll(() => {
  mkdirSync(artifactDirectory, { recursive: true });
  mkdirSync(repairArtifactDirectory, { recursive: true });
});

const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
] as const;

async function openDashboard(page: Page, url = "/dashboard") {
  await page.goto(url, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "信号轨道" })).toBeVisible();
  await expect(page.getByText("数据截至 16:00")).toBeVisible();
}

async function contrastRatio(locator: ReturnType<Page["getByText"]>) {
  return locator.evaluate((element) => {
    const parseColor = (value: string) => {
      const channels = value.match(/[\d.]+/g)?.map(Number) ?? [];
      return {
        red: channels[0] ?? 0,
        green: channels[1] ?? 0,
        blue: channels[2] ?? 0,
        alpha: channels[3] ?? 1,
      };
    };
    const luminance = (red: number, green: number, blue: number) => {
      const convert = (channel: number) => {
        const normalized = channel / 255;
        return normalized <= 0.04045
          ? normalized / 12.92
          : ((normalized + 0.055) / 1.055) ** 2.4;
      };
      return (
        0.2126 * convert(red) + 0.7152 * convert(green) + 0.0722 * convert(blue)
      );
    };

    const foreground = parseColor(getComputedStyle(element).color);
    let ancestor: Element | null = element;
    let background = { red: 255, green: 255, blue: 255, alpha: 1 };
    while (ancestor) {
      const candidate = parseColor(getComputedStyle(ancestor).backgroundColor);
      if (candidate.alpha > 0) {
        background = candidate;
        break;
      }
      ancestor = ancestor.parentElement;
    }

    const foregroundLuminance = luminance(
      foreground.red,
      foreground.green,
      foreground.blue,
    );
    const backgroundLuminance = luminance(
      background.red,
      background.green,
      background.blue,
    );
    return (
      (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
      (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
    );
  });
}

for (const viewport of viewports) {
  test(`E03 normal layout ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await openDashboard(page);

    const rail = page.getByRole("list", {
      name: "采集到发布的固定八阶段",
    });
    await expect(rail.getByRole("listitem")).toHaveCount(8);
    await expect(page.getByText("SAVED", { exact: true })).toBeVisible();
    await expect(page.getByText("RECEIVED", { exact: true })).toBeVisible();
    await expect(rail.getByText("30 Hz 对齐", { exact: true })).toBeVisible();
    await expect(rail.getByText("数据标注", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /进入发布/ })).toBeVisible();
    await expect(page.getByText("清洗", { exact: true })).toHaveCount(0);

    const geometry = await page.evaluate(() => {
      const pending = document.querySelector(
        '[aria-labelledby="dashboard-pending-title"]',
      );
      const side = document.querySelector('[aria-label="最近活动与局部状态"]');
      const rail = document.querySelector(
        '[aria-label="采集到发布的固定八阶段"]',
      );
      if (
        !(pending instanceof HTMLElement) ||
        !(side instanceof HTMLElement) ||
        !(rail instanceof HTMLElement)
      ) {
        throw new Error("E03 layout nodes are missing");
      }
      return {
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        pendingWidth: pending.getBoundingClientRect().width,
        sideWidth: side.getBoundingClientRect().width,
        railOverflow: rail.scrollWidth - rail.clientWidth,
      };
    });

    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
    expect(geometry.pendingWidth / geometry.sideWidth).toBeGreaterThan(1.55);
    expect(geometry.pendingWidth / geometry.sideWidth).toBeLessThan(2.3);
    expect(geometry.railOverflow).toBeLessThanOrEqual(1);

    const publishLink = page.getByRole("link", { name: /进入发布/ });
    await publishLink.focus();
    const focus = await publishLink.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
      };
    });
    expect(focus.outlineStyle).not.toBe("none");
    expect(Number.parseFloat(focus.outlineWidth)).toBeGreaterThanOrEqual(2);

    expect(
      await contrastRatio(page.getByRole("heading", { name: "工作台" })),
    ).toBeGreaterThanOrEqual(4.5);
    expect(await contrastRatio(publishLink)).toBeGreaterThanOrEqual(4.5);

    const unnamedInteractiveNodes = await page.evaluate(() =>
      [...document.querySelectorAll("a, button, input, select, textarea")]
        .filter((element) => {
          const htmlElement = element as HTMLElement;
          return (
            htmlElement.offsetParent !== null &&
            !(
              element.getAttribute("aria-label") ||
              element.getAttribute("aria-labelledby") ||
              element.textContent?.trim() ||
              (element instanceof HTMLInputElement && element.placeholder)
            )
          );
        })
        .map((element) => element.outerHTML),
    );
    expect(unnamedInteractiveNodes).toEqual([]);

    await page.screenshot({
      path: path.join(artifactDirectory, `${viewport.name}.png`),
      animations: "disabled",
      fullPage: false,
    });

    const projectName = page.getByText("prj_fx_01", { exact: true }).first();
    await expect(projectName).toBeVisible();
    const longProjectLayout = await projectName.evaluate((element) => {
      const selectionItem =
        element.closest(".ant-select-selection-item") ?? element;
      if (!(selectionItem instanceof HTMLElement)) {
        throw new Error("Current project selector value is missing");
      }
      element.textContent =
        "华东机器人多模态数据闭环验证与量产交付超长项目名称";
      return {
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        itemWidth: selectionItem.clientWidth,
        itemScrollWidth: selectionItem.scrollWidth,
      };
    });
    expect(longProjectLayout.documentWidth).toBeLessThanOrEqual(
      longProjectLayout.viewportWidth,
    );
    expect(longProjectLayout.itemScrollWidth).toBeGreaterThan(
      longProjectLayout.itemWidth,
    );

    if (viewport.name === "1440x900") {
      const rangeSelect = page.getByRole("combobox", { name: "时间范围" });
      await rangeSelect.click();
      await rangeSelect.press("ArrowDown");
      await rangeSelect.press("Enter");
      await expect
        .poll(() => new URL(page.url()).searchParams.get("range"))
        .toBe("7d");
    }
  });
}

test("E03 transport failure degrades only the coverage region", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/dashboard?mockScenario=dashboard:partial-error", {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByRole("heading", { name: "信号轨道" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "我的待办" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近活动" })).toBeVisible();
  await expect(page.getByText("此区域暂时不可用")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("req_fx_dashboard_coverage_503")).toBeVisible();
  await expect(page.getByRole("link", { name: /进入发布/ })).toBeVisible();
});

test("E03 empty state keeps the same information architecture", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openDashboard(page);
  await page.evaluate(
    "import('/src/mocks/scenarios/dashboard.ts').then((module) => module.setDashboardScenario('empty'))",
  );
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect(page.getByText("当前时段暂无待办")).toBeVisible();
  await expect(page.getByText("当前时段暂无活动")).toBeVisible();
  await expect(
    page
      .getByRole("list", { name: "采集到发布的固定八阶段" })
      .getByRole("listitem"),
  ).toHaveCount(8);

  const horizontalScroll = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(horizontalScroll).toBeLessThanOrEqual(0);

  await page.screenshot({
    path: path.join(artifactDirectory, "empty-1440x900.png"),
    animations: "disabled",
    fullPage: false,
  });
});

test("E03 keeps the real pending route usable at 375x812", async ({ page }) => {
  mkdirSync(repairArtifactDirectory, { recursive: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await openDashboard(page);
  await expect(page.getByRole("heading", { name: "我的待办" })).toBeVisible();
  await expect(
    page.getByRole("list", { name: "授权范围内的待办事项" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    ),
  ).toBeLessThanOrEqual(0);
  await page.screenshot({
    path: path.resolve(repairArtifactDirectory, "375x812-todo.png"),
    animations: "disabled",
    fullPage: false,
  });
});
