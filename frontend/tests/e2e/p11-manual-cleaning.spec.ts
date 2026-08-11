import { expect, test } from '@playwright/test';

test.describe('P11 手动清洗工作台', () => {
  test('复用 cleaning 播放内核并经危险确认提交', async ({ page }) => {
    await page.goto('/manual/drafts/draft_fx_mc_issue_01?compare=ab&mockScenario=cleaning:happy');
    await expect(page.getByRole('heading', { name: '手动清洗工作台' })).toBeVisible();
    await expect(page.locator('.episode-workbench-core')).toHaveAttribute('data-mode', 'cleaning');
    await expect(page.getByRole('link', { name: 'A/B' })).toHaveAttribute('aria-current', 'page');
    await page.getByRole('button', { name: '提交版本' }).click();
    const dialog = page.getByRole('dialog', { name: '确认提交清洗 Draft' });
    await expect(dialog.getByText('draft_fx_mc_issue_01')).toBeVisible();
    await expect(dialog.getByRole('heading', { name: '影响摘要' })).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'blocked_reasons' })).toBeVisible();
    await dialog.getByRole('checkbox').check();
    await dialog.getByRole('button', { name: '确认危险提交' }).click();
    await expect(dialog).toBeHidden();
  });

  test('ReviewFinding 永久只读并只定位显式半开区间', async ({ page }) => {
    await page.goto('/manual/drafts/draft_fx_mc_issue_01?findingId=finding_fx_mc_02&mockScenario=cleaning:returned');
    await expect(page.getByRole('heading', { name: '复核退回定位（只读）' })).toBeVisible();
    await expect(page.getByText('当前半开范围：[200000000, 450000000) ns', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /解决 Finding|删除 Finding|退回复核/ })).toHaveCount(0);
    await expect(page.getByRole('link', { name: /打开后继草稿/ })).toHaveAttribute('href', /draft_fx_mc_successor_01/);
  });
});
