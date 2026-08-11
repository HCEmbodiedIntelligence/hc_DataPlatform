import { expect, test } from '@playwright/test';

test('P18 用户权限主流程可渲染', async ({ page }) => {
  await page.goto('/settings/access?mockScenario=management:happy');
  await expect(page.getByRole('heading', { name: '用户权限' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Fixture 管理员' })).toBeVisible();
});
