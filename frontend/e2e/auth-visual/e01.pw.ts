import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const isFe12Run = process.env.HC_REAL_API_E2E_RUN_OWNER === "fe12";
const isFe14Run = process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14";
const artifactRoot = resolve(
  process.cwd(),
  isFe14Run
    ? "../artifacts/visual/e01-e10/FE14-final/fixture/E01"
    : isFe12Run
      ? "../artifacts/visual/e01-e10/FE12-final/E01"
      : "../artifacts/visual/e01-e10/E01",
);
const repairArtifactRoot = resolve(
  process.cwd(),
  isFe14Run
    ? "../artifacts/visual/e01-e10/FE14-final/fixture"
    : isFe12Run
      ? "../artifacts/visual/e01-e10/FE12-final"
      : "../artifacts/visual/e01-e10/FE11-repair",
);

test.beforeAll(() => {
  mkdirSync(artifactRoot, { recursive: true });
  mkdirSync(repairArtifactRoot, { recursive: true });
});

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(overflow.content).toBeLessThanOrEqual(overflow.viewport);
}

test("captures the deterministic E01 three-state visual fixture", async ({
  page,
}) => {
  await page.goto("/__visual__/e01");
  await expect(
    page.getByRole("heading", { level: 1, name: "登录" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "账户创建成功" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "尚未加入项目" }),
  ).toBeVisible();
  await expect(page.getByRole("img", { name: "杭叉集团" })).toHaveAttribute(
    "src",
    /hangcha-logo\.png/u,
  );
  await expectNoPageOverflow(page);
  await page.screenshot({ path: `${artifactRoot}/1440x900.png` });

  await page.setViewportSize({ width: 1280, height: 800 });
  await expectNoPageOverflow(page);
  await page.screenshot({ path: `${artifactRoot}/1280x800.png` });
});

test("captures a stable rate-limit state without account enumeration", async ({
  page,
}) => {
  await page.goto("/__visual__/e01?error=429");
  await expect(page.getByText(/尝试次数过多/u)).toBeVisible();
  await expect(page.getByText(/平台不会显示账户是否存在/u)).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: `${artifactRoot}/429-rate-limit.png` });
});

test("keeps the public login route keyboard-first and usable at 375 px", async ({
  page,
}) => {
  mkdirSync(repairArtifactRoot, { recursive: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/auth/login");
  await expect(
    page.getByRole("heading", { level: 1, name: "登录" }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "跳到认证内容" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#auth-content")).toBeFocused();

  await expectNoPageOverflow(page);
  const username = page.getByLabel("用户名");
  const password = page.getByLabel("密码", { exact: true });
  await expect(username).toBeVisible();
  await expect(password).toBeVisible();
  expect(
    await username.evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).fontSize),
    ),
  ).toBeGreaterThanOrEqual(16);
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo({ top: 0 });
  });
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${artifactRoot}/375x812-login.png` });
  await page.screenshot({
    path: resolve(repairArtifactRoot, "375x812-login.png"),
  });
});
