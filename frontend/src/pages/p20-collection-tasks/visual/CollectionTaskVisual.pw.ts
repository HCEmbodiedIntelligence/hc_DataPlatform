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
    target: { package_count: 240, duration_seconds: 72000 },
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
    target: { duration_seconds: 21600 },
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

interface VisualProgressSummary {
  readonly received: number;
  readonly capturedDuration: number;
  readonly durationUnknown: number;
  readonly pass: number;
  readonly risk: number;
  readonly reject: number;
  readonly pending: number;
  readonly qualityStatus: "NOT_CONFIGURED" | "PENDING_QC" | "NOT_MET" | "MET";
}

const progressById: Readonly<Record<string, VisualProgressSummary>> = {
  "task-transparent-parts": {
    received: 146,
    capturedDuration: 55200,
    durationUnknown: 2,
    pass: 126,
    risk: 8,
    reject: 5,
    pending: 7,
    qualityStatus: "PENDING_QC",
  },
  "task-pallet": {
    received: 98,
    capturedDuration: 36000,
    durationUnknown: 3,
    pass: 65,
    risk: 14,
    reject: 12,
    pending: 7,
    qualityStatus: "NOT_MET",
  },
  "task-forklift": {
    received: 37,
    capturedDuration: 9000,
    durationUnknown: 0,
    pass: 31,
    risk: 3,
    reject: 1,
    pending: 2,
    qualityStatus: "NOT_CONFIGURED",
  },
  "task-seal": {
    received: 96,
    capturedDuration: 43200,
    durationUnknown: 0,
    pass: 88,
    risk: 3,
    reject: 2,
    pending: 3,
    qualityStatus: "MET",
  },
  "task-cable": {
    received: 64,
    capturedDuration: 28800,
    durationUnknown: 1,
    pass: 55,
    risk: 4,
    reject: 3,
    pending: 2,
    qualityStatus: "MET",
  },
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
  await page.route(
    "**/api/v1/account/notifications/unread-count",
    async (route) => {
      await fulfillJson(route, { unread_count: 0 });
    },
  );

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
      const definition = tasks.find((task) => task.collection_task_id === id);
      const summary = progressById[id] ?? {
        received: 0,
        capturedDuration: 0,
        durationUnknown: 0,
        pass: 0,
        risk: 0,
        reject: 0,
        pending: 0,
        qualityStatus: "NOT_CONFIGURED",
      };
      const packageTarget =
        definition?.target && "package_count" in definition.target
          ? (definition.target.package_count ?? null)
          : null;
      const durationTarget =
        definition?.target && "duration_seconds" in definition.target
          ? (definition.target.duration_seconds ?? null)
          : null;
      const metric = (actual: number, target: number | null) =>
        target === null
          ? null
          : {
              actual,
              target,
              progress: actual / target,
              status:
                actual > target
                  ? "EXCEEDED"
                  : actual === target
                    ? "MET"
                    : "IN_PROGRESS",
            };
      const packageMetric = metric(summary.received, packageTarget);
      const durationMetric = metric(summary.capturedDuration, durationTarget);
      const targetStatuses = [
        packageMetric?.status,
        durationMetric?.status,
      ].filter(
        (status): status is "IN_PROGRESS" | "MET" | "EXCEEDED" =>
          status !== undefined,
      );
      const attainmentStatus =
        targetStatuses.length === 0
          ? "NOT_CONFIGURED"
          : targetStatuses.every((status) => status === "MET")
            ? "ATTAINED"
            : targetStatuses.every((status) => status !== "IN_PROGRESS")
              ? "EXCEEDED"
              : "IN_PROGRESS";
      const evaluated = summary.pass + summary.risk + summary.reject;
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
        captured_duration_seconds: summary.capturedDuration,
        duration_observed_package_count:
          summary.received - summary.durationUnknown,
        duration_unknown_package_count: summary.durationUnknown,
        qc: {
          evaluated_count: evaluated,
          pass_count: summary.pass,
          risk_count: summary.risk,
          reject_count: summary.reject,
          pending_count: summary.pending,
          pass_rate: {
            numerator: summary.pass,
            denominator: evaluated,
            value: evaluated > 0 ? summary.pass / evaluated : null,
          },
        },
        attainment: {
          status: attainmentStatus,
          package_count: packageMetric,
          duration_seconds: durationMetric,
          quality_threshold: definition?.quality_threshold ?? null,
          quality_status: summary.qualityStatus,
        },
        observed_sources: {
          device_ids: [`robot-${id.slice(-4)}`],
          camera_ids: [`camera-${id.slice(-4)}`],
          topic_names: id === "task-transparent-parts" ? ["/vision/rgb"] : [],
        },
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
    if (message.type() !== "error") return;
    const location = message.location();
    consoleErrors.push(
      location.url
        ? `${message.text()} (${location.url}:${location.lineNumber})`
        : message.text(),
    );
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
    async ({
      organizationId: activeOrganizationId,
      projectId: activeProjectId,
    }) => {
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
        organizationId: activeOrganizationId,
        projectId: activeProjectId,
        regionCode: "cn-east-01",
      });
      store.setSessionScopes(
        [
          {
            organizationId: activeOrganizationId,
            projectId: activeProjectId,
            regionCodes: ["cn-east-01"],
            projectWide: false,
            capabilities: ["upload.manage"],
          },
        ],
        1,
      );
      store.setAuthorization({
        scopeKey: useShellStore.getState().scopeKey,
        roleVersion: "e04-visual-v1",
        capabilities: ["upload.manage"],
        fetchedAt: "2026-08-18T05:30:00Z",
      });
    },
    { organizationId, projectId },
  );
}

test.beforeAll(async () => {
  await mkdir(artifactDirectory, { recursive: true });
});

for (const viewport of [
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "375x812", width: 375, height: 812 },
] as const) {
  test(`E04 task list ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    const consoleErrors = await preparePage(page);
    await page.goto("/collection-tasks", { waitUntil: "domcontentloaded" });
    await installVisualScope(page);
    await page.waitForLoadState("networkidle");

    const listPanel = page.getByRole("region", { name: "采集任务列表" });
    await expect(listPanel).toBeVisible();
    await expect(
      page.getByText("任务码用于数据归类，不是数据包 ID。"),
    ).toHaveCount(0);
    await expect(page.getByText("搜索与类型仅筛选当前游标窗口")).toHaveCount(0);
    await expect(page.getByText("目标进行中").first()).toBeVisible();
    await expect(page.getByText("146 包 / 240 包").first()).toBeVisible();
    await expect(
      page.getByText("15 小时 20 分 / 20 小时").first(),
    ).toBeVisible();
    await expect(page.getByText("未达到阈值").first()).toBeVisible();
    await expect(
      page.getByRole("link", { name: "查看数据" }).first(),
    ).toBeVisible();
    await expect(listPanel.getByLabel("当前项目")).toHaveCount(0);
    await expect(page.getByText(/SAVED/u)).toHaveCount(0);

    const geometry = await page.evaluate(() => {
      const panel = document.querySelector<HTMLElement>(
        'section[aria-label="采集任务列表"]',
      );
      const tableScroll =
        panel?.querySelector<HTMLElement>(".ant-table-content");
      const mobileCards = panel?.querySelectorAll("article") ?? [];
      const mobileButtons = Array.from(
        document.querySelectorAll<HTMLElement>(
          '[data-page-id="P20"] button, [data-page-id="P20"] a',
        ),
      ).filter((element) => {
        const style = getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden")
          return false;
        return (
          element.tagName === "BUTTON" ||
          element.textContent?.includes("查看数据") === true
        );
      });
      if (!panel) throw new Error("P20 list panel is missing.");
      const panelRect = panel.getBoundingClientRect();
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        panelLeft: panelRect.left,
        panelRight: panelRect.right,
        tableOverflow:
          tableScroll == null
            ? 0
            : Math.max(0, tableScroll.scrollWidth - tableScroll.clientWidth),
        mobileCardCount: mobileCards.length,
        tableCount: panel.querySelectorAll("table").length,
        minimumActionHeight:
          mobileButtons.length === 0
            ? 0
            : Math.min(
                ...mobileButtons.map(
                  (element) => element.getBoundingClientRect().height,
                ),
              ),
      };
    });
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
    expect(geometry.panelLeft).toBeGreaterThanOrEqual(0);
    expect(geometry.panelRight).toBeLessThanOrEqual(geometry.viewportWidth + 1);
    if (viewport.width === 375) {
      expect(geometry.mobileCardCount).toBe(tasks.length);
      expect(geometry.tableCount).toBe(0);
      expect(geometry.minimumActionHeight).toBeGreaterThanOrEqual(44);
    } else {
      expect(geometry.tableCount).toBe(1);
      expect(geometry.mobileCardCount).toBe(0);
      expect(geometry.tableOverflow).toBeLessThanOrEqual(1);
    }
    expect(consoleErrors).toEqual([]);

    await assertNoSeriousOrCriticalAxe(
      page,
      path.join(artifactDirectory, `axe-list-${viewport.name}.json`),
      '[data-page-id="P20"]',
    );
    await page.screenshot({
      path: path.join(artifactDirectory, `list-${viewport.name}.png`),
      animations: "disabled",
      fullPage: viewport.width === 375,
    });
  });
}

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

  await expect(
    page.getByRole("heading", { name: "采集任务", exact: true }),
  ).toBeVisible();
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
