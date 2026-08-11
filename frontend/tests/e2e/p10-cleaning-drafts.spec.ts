import { expect, test } from '@playwright/test';

test.describe('P10 清洗草稿', () => {
  test('只读聚合、Inspector 与 P11 交接', async ({ page }) => {
    await page.goto('/manual/drafts?mockScenario=cleaning:happy');
    await expect(page.getByRole('heading', { name: '清洗草稿' })).toBeVisible();
    await expect(page.getByText('draft_fx_mc_issue_01').first()).toBeVisible();
    await page.getByRole('button', { name: 'draft_fx_mc_issue_01' }).click();
    const inspector = page.getByRole('dialog', { name: '草稿详情' });
    await expect(inspector).toBeVisible();
    await expect(inspector.getByText('ManualIssue 来源')).toBeVisible();
    await inspector.getByRole('link', { name: '打开清洗工作台' }).click();
    await expect(page).toHaveURL(/\/manual\/drafts\/draft_fx_mc_issue_01/);
    await expect(page.locator('.episode-workbench-core')).toHaveAttribute('data-mode', 'cleaning');
  });

  test('RETURNED 原草稿只把返工交给唯一 successor', async ({ page }) => {
    await page.goto('/manual/drafts?scope=returned&mockScenario=cleaning:returned');
    await expect(page.getByText('复核退回').first()).toBeVisible();
    const row = page.getByRole('row').filter({ hasText: 'draft_fx_mc_issue_01' });
    await row.getByRole('link', { name: '返工' }).click();
    await expect(page).toHaveURL(/\/manual\/drafts\/draft_fx_mc_successor_01/);
    await expect(page.getByText('Review Return').first()).toBeVisible();
  });
});
