import { expect, test } from "@playwright/test";

test("registers, logs in, and reaches the authoritative empty-account state", async ({
  page,
}) => {
  const username = `fe02-${Date.now()}`;
  const password = "FE02-real-api-password-9!";

  await page.goto("/auth/register");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("确认密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "创建账户" }).click();

  await expect(page).toHaveURL(/\/auth\/registered$/u);
  await expect(
    page.getByRole("heading", { name: "账户创建成功" }),
  ).toBeVisible();
  await expect(page.getByLabel("用户名")).toHaveValue(username);

  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("密码", { exact: true }).press("Enter");

  await expect(page).toHaveURL(/\/account\/empty$/u);
  await expect(
    page.getByRole("heading", { level: 1, name: "尚未加入项目" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "申请加入项目" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "申请权限" })).toBeVisible();
  await expect(page.getByRole("button", { name: "申请记录" })).toBeVisible();
});
