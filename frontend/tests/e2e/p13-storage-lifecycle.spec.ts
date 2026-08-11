import { expect, test } from '@playwright/test';

test('P13 生命周期主流程可由冻结 MSW fixture 渲染', async ({ page }) => {
  await page.goto('/storage/lifecycle?mockScenario=management:happy');
  await expect(page.getByRole('heading', { name: '生命周期' })).toBeVisible();
  await expect(page.getByText('冷数据转 IA')).toBeVisible();
});
