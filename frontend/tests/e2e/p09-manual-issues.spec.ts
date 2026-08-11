import { expect, test } from '@playwright/test';

test.describe('P09 人工问题', () => {
  test('分诊清单与 Issue → Draft 只按服务端 draftId 进入 P11', async ({ page }) => {
    await page.goto('/manual/issues?mockScenario=cleaning:happy');
    await expect(page.getByRole('heading', { name: '人工问题' })).toBeVisible();
    await expect(page.getByText('issue_fx_mc_open_01')).toBeVisible();
    await expect(page.locator('body')).not.toContainText('ReviewFinding 状态');
    await page.getByRole('button', { name: '创建草稿' }).click();
    await expect(page).toHaveURL(/\/manual\/drafts\/draft_fx_mc_issue_01$/);
    await expect(page.getByRole('heading', { name: '手动清洗工作台' })).toBeVisible();
  });

  test('空态和权限态不暴露 Review mutation 或删除入口', async ({ page }) => {
    await page.goto('/manual/issues?mockScenario=cleaning:empty');
    await expect(page.getByText('当前作用域没有人工问题')).toBeVisible();
    await expect(page.getByRole('button', { name: /删除/ })).toHaveCount(0);
    await page.goto('/manual/issues?mockScenario=cleaning:forbidden');
    await expect(page.getByRole('heading', { name: '无权访问' })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('issue_fx_mc_open_01');
  });
});
