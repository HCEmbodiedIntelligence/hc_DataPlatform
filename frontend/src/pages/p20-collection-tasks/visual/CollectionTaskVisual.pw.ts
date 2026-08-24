import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { assertNoSeriousOrCriticalAxe } from "../../../../e2e/visual-support/axe";

const artifactDirectory = path.resolve(
  process.cwd(),
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe16"
    ? "../artifacts/visual/e01-e10/FE16-final/fixture/E04"
    : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
      ? "../artifacts/visual/e01-e10/FE14-final/fixture/E04"
      : process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12"
        ? "../artifacts/visual/e01-e10/FE12-final/E04"
        : "../artifacts/visual/e01-e10/E04",
);

const projectId = "project-dual-arm-01";
const organizationId = "organization-dual-arm";
const tasks = [
  {
    schema_version: "1",
    collection_task_id: "task-transparent-parts",
    organization_id: organizationId,
    project_id: projectId,
    task_code: "00000042",
    name: "透明件抓取多视角采集",
    type: "抓取采集",
    scenario: "透明工件装配工位",
    description: "覆盖反光、遮挡与不同夹爪姿态。",
    target: { package_count: 240 },
    quality_threshold: 0.9,
    status: "ACTIVE",
  },
  {
    schema_version: "1",
    collection_task_id: "task-pallet",
    organization_id: organizationId,
    project_id: projectId,
    task_code: "00000039",
    name: "托盘搬运夜班数据补采",
    type: "搬运采集",
    scenario: "低照度仓储通道",
    description: "补充低照度、逆光和动态人员干扰样本。",
    target: { package_count: 180 },
    quality_threshold: 0.86,
    status: "ACTIVE",
  },
  {
    schema_version: "1",
    collection_task_id: "task-forklift",
    organization_id: organizationId,
    project_id: projectId,
    task_code: "00000035",
    name: "叉车协同避障基线采集",
    type: "协同采集",
    scenario: "人车混行测试区",
    description: "记录典型会车、让行与临时障碍条件。",
    target: { package_count: 120 },
    quality_threshold: null,
    status: "ACTIVE",
  },
  {
    schema_version: "1",
    collection_task_id: "task-seal",
    organization_id: organizationId,
    project_id: projectId,
    task_code: "00000028",
    name: "密封圈装配质量回归采集",
    type: "装配采集",
    scenario: "柔性装配单元",
    description: "已完成本轮目标，历史数据保留查询。",
    target: { package_count: 96 },
    quality_threshold: 0.92,
    status: "CLOSED",
  },
  {
    schema_version: "1",
    collection_task_id: "task-cable",
    organization_id: organizationId,
    project_id: projectId,
    task_code: "00000021",
    name: "线束插接精细动作采集",
    type: "装配采集",
    scenario: "精密线束工位",
    description: "采集插接全过程与失败恢复样本。",
    target: null,
    quality_threshold: 0.88,
    status: "CLOSED",
  },
] as const;

const progressById: Readonly<
  Record<string, { received: number; pass: number; evaluated: number }>
> = {
  "task-transparent-parts": { received: 146, pass: 126, evaluated: 139 },
  "task-pallet": { received: 98, pass: 83, evaluated: 91 },
  "task-forklift": { received: 37, pass: 31, evaluated: 35 },
  "task-seal": { received: 96, pass: 88, evaluated: 93 },
  "task-cable": { received: 64, pass: 55, evaluated: 62 },
};

async function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
  headers: Readonly<Record<string, string>> = {},
) {
  await route.fulfill({
    status,
    headers: {
      "Content-Type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

async function installApiFixture(
  page: Page,
  options: Readonly<{ listError?: boolean }> = {},
) {
  await page.route("**/api/v1/projects/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      options.listError &&
      request.method() === "GET" &&
      url.pathname.endsWith("/collection-tasks")
    ) {
      await fulfillJson(
        route,
        {
          type: "about:blank",
          title: "Too many requests",
          status: 429,
          detail: "采集任务读取频率受限，请在 30 秒后重试。",
          code: "COLLECTION_TASK_RATE_LIMITED",
          request_id: "request-e04-rate-limited",
          retryable: true,
        },
        429,
        { "Content-Type": "application/problem+json", "Retry-After": "30" },
      );
      return;
    }

    const progressMatch = /\/collection-tasks\/([^/]+)\/progress$/u.exec(
      url.pathname,
    );
    if (progressMatch?.[1]) {
      const id = decodeURIComponent(progressMatch[1]);
      const summary = progressById[id] ?? {
        received: 0,
        pass: 0,
        evaluated: 0,
      };
      await fulfillJson(route, {
        schema_version: "1",
        collection_task_id: id,
        organization_id: organizationId,
        project_id: projectId,
        status:
          tasks.find((task) => task.collection_task_id === id)?.status ??
          "ACTIVE",
        as_of: "2026-08-18T05:30:00Z",
        received_package_count: summary.received,
        captured_duration_seconds: summary.received * 60,
        duration_observed_package_count: summary.received,
        duration_unknown_package_count: 0,
        qc: {
          evaluated_count: summary.evaluated,
          pass_count: summary.pass,
          risk_count: Math.max(0, summary.evaluated - summary.pass),
          reject_count: 0,
          pending_count: Math.max(0, summary.received - summary.evaluated),
          pass_rate: {
            numerator: summary.pass,
            denominator: summary.evaluated,
            value:
              summary.evaluated > 0 ? summary.pass / summary.evaluated : null,
          },
        },
        attainment: {
          status: "NOT_CONFIGURED",
          package_count: null,
          duration_seconds: null,
          quality_threshold: null,
          quality_status: "NOT_CONFIGURED",
        },
        observed_sources: { device_ids: [], camera_ids: [], topic_names: [] },
      });
      return;
    }

    const detailMatch = /\/collection-tasks\/([^/]+)$/u.exec(url.pathname);
    if (detailMatch?.[1] && request.method() === "GET") {
      const task = tasks.find(
        (candidate) =>
          candidate.collection_task_id ===
          decodeURIComponent(detailMatch[1] ?? ""),
      );
      await fulfillJson(route, task ?? tasks[0], task ? 200 : 404, {
        ETag: '"visual-v1"',
      });
      return;
    }

    await fulfillJson(route, {
      items: tasks,
      next_cursor: null,
    });
  });
}

async function preparePage(page: Page, listError = false) {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await page.addInitScript(() => {
    window.localStorage.setItem("hc-platform-navigation-collapsed", "true");
  });
  await installApiFixture(page, { listError });
  return consoleErrors;
}

async function installVisualScope(page: Page) {
  await page.evaluate(
    async ({ projectId: activeProjectId }) => {
      const storeModulePath = "/src/shared/scope/shell-store.ts";
      const { useShellStore } = await import(
        /* @vite-ignore */ storeModulePath
      );
      const store = useShellStore.getState();
      store.setSession(
        {
          actorId: "actor-e04-visual",
          displayName: "张驰",
          roleIds: ["PROJECT_ADMIN"],
        },
        "e04-visual-session",
      );
      store.setScope({
        organizationId: "org-e04-visual",
        projectId: activeProjectId,
        regionCode: "cn-east-01",
      });
      store.setSessionScopes(
        [
          {
            projectId: activeProjectId,
            regionCodes: ["cn-east-01"],
            projectWide: false,
            capabilities: ["collection.upload"],
          },
        ],
        1,
      );
      store.setAuthorization({
        scopeKey: useShellStore.getState().scopeKey,
        roleVersion: "e04-visual-v1",
        capabilities: ["collection.upload"],
        fetchedAt: "2026-08-18T05:30:00Z",
      });
    },
    { projectId },
  );
}

test.beforeAll(async () => {
  await mkdir(artifactDirectory, { recursive: true });
});

for (const viewport of [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
] as const) {
  test(`E04 create drawer ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    const consoleErrors = await preparePage(page);
    await page.goto("/collection-tasks?drawer=create", {
      waitUntil: "domcontentloaded",
    });
    await installVisualScope(page);
    await page.waitForLoadState("networkidle");

    const drawer = page.getByRole("dialog", { name: "新建采集任务" });
    await expect(drawer).toBeVisible();
    await expect(page.getByText("透明件抓取多视角采集")).toBeVisible();
    await expect(
      page.getByText("进行中", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("已关闭", { exact: true }).first(),
    ).toBeVisible();
    await expect(drawer.getByLabel("任务名称")).toBeFocused();

    for (const label of [
      "项目",
      "任务名称",
      "任务编号（自动生成）",
      "采集类型",
      "采集场景",
      "任务描述",
      "目标数据包数量",
      "目标总时长（小时）",
      "质量通过阈值（%）",
    ]) {
      await expect(drawer.getByLabel(label, { exact: true })).toHaveCount(1);
    }

    for (const forbidden of [
      "分派",
      "人员",
      "PICO",
      "机器人",
      "开始时间",
      "结束时间",
      "暂停",
      "继续",
      "模态",
      "Topic",
    ]) {
      await expect(drawer.getByText(new RegExp(forbidden, "iu"))).toHaveCount(
        0,
      );
    }

    const geometry = await page.evaluate(() => {
      const frame = document.querySelector('[data-page-id="P20"]');
      const drawer = document.querySelector(
        '[data-testid="collection-task-drawer"]',
      );
      const sider = document.querySelector(".ant-layout-sider");
      const firstLabel = drawer?.querySelector(".ant-form-item-label label");
      const focused = document.activeElement;
      if (
        !(frame instanceof HTMLElement) ||
        !(drawer instanceof HTMLElement) ||
        !(sider instanceof HTMLElement) ||
        !(firstLabel instanceof HTMLElement) ||
        !(focused instanceof HTMLElement)
      ) {
        throw new Error("E04 layout nodes are missing.");
      }
      const rgb = (value: string) =>
        (value.match(/[\d.]+/gu) ?? []).slice(0, 3).map(Number);
      const luminance = (value: string) => {
        const channels = rgb(value).map((channel) => {
          const normalized = channel / 255;
          return normalized <= 0.04045
            ? normalized / 12.92
            : ((normalized + 0.055) / 1.055) ** 2.4;
        });
        return (
          (channels[0] ?? 0) * 0.2126 +
          (channels[1] ?? 0) * 0.7152 +
          (channels[2] ?? 0) * 0.0722
        );
      };
      const labelLuminance = luminance(getComputedStyle(firstLabel).color);
      const surfaceLuminance = luminance(
        getComputedStyle(drawer).backgroundColor,
      );
      const focusedStyle = getComputedStyle(focused);
      const focusContainer =
        focused.closest(".ant-input-affix-wrapper") ?? focused;
      const focusContainerStyle = getComputedStyle(focusContainer);
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        frameWidth: frame.getBoundingClientRect().width,
        drawerWidth: drawer.getBoundingClientRect().width,
        siderWidth: sider.getBoundingClientRect().width,
        labelContrast:
          (Math.max(labelLuminance, surfaceLuminance) + 0.05) /
          (Math.min(labelLuminance, surfaceLuminance) + 0.05),
        hasVisibleFocusIndicator:
          (focusedStyle.outlineStyle !== "none" &&
            Number.parseFloat(focusedStyle.outlineWidth) >= 2) ||
          focusContainerStyle.boxShadow !== "none",
      };
    });
    const expectedDrawerWidth = Math.min(
      540,
      Math.max(440, viewport.width * 0.34),
    );
    expect(
      Math.abs(geometry.drawerWidth - expectedDrawerWidth),
    ).toBeLessThanOrEqual(1);
    expect(geometry.drawerWidth / geometry.frameWidth).toBeGreaterThan(0.33);
    expect(geometry.drawerWidth / geometry.frameWidth).toBeLessThan(0.43);
    expect(geometry.siderWidth).toBe(64);
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
    expect(geometry.labelContrast).toBeGreaterThanOrEqual(4.5);
    expect(geometry.hasVisibleFocusIndicator).toBe(true);
    expect(consoleErrors).toEqual([]);
    await assertNoSeriousOrCriticalAxe(
      page,
      path.join(artifactDirectory, `axe-${viewport.name}.json`),
      '[data-page-id="P20"]',
    );

    // Keep the focus-visible assertion above, but avoid including a blinking
    // native input caret in the pixel baseline.
    await page.evaluate(() =>
      (document.activeElement as HTMLElement | null)?.blur(),
    );

    await page.screenshot({
      path: path.join(artifactDirectory, `${viewport.name}.png`),
      animations: "disabled",
      fullPage: false,
    });

    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();
  });
}

test("E04 429 abnormal state", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const consoleErrors = await preparePage(page, true);
  await page.goto("/collection-tasks", { waitUntil: "domcontentloaded" });
  await installVisualScope(page);
  await page.waitForLoadState("networkidle");

  await expect(page.getByRole("heading", { name: "采集任务" })).toBeVisible();
  await expect(page.getByText("请求频率受限")).toBeVisible();
  await expect(page.getByText("request-e04-rate-limited")).toBeVisible();
  await expect(page.getByRole("button", { name: /重\s*试/u })).toBeVisible();
  expect(
    consoleErrors.filter((message) => !message.includes("status of 429")),
  ).toEqual([]);

  await page.screenshot({
    path: path.join(artifactDirectory, "429-error-1440x900.png"),
    animations: "disabled",
    fullPage: false,
  });
});
