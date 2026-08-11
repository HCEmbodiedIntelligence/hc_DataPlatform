import { expect, test } from '@playwright/test';

test('P15 机器人与组件主流程可渲染', async ({ page }) => {
  await page.goto('/settings/robots?mockScenario=management:happy');
  await expect(page.getByRole('heading', { name: '机器人与组件' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Assembly Robot 01' })).toBeVisible();
});
