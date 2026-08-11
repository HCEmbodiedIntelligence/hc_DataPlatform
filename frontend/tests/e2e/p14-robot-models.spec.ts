import { expect, test } from '@playwright/test';

test('P14 机器人模型资产主流程可渲染', async ({ page }) => {
  await page.goto('/settings/robot-models?mockScenario=management:happy');
  await expect(page.getByRole('heading', { name: '机器人模型资产' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'HC Assembly Arm' })).toBeVisible();
});
