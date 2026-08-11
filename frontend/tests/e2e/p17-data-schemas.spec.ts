import { expect, test } from '@playwright/test';

test('P17 数据 Schema 主流程可渲染', async ({ page }) => {
  await page.goto('/settings/data-schemas?mockScenario=management:happy');
  await expect(page.getByRole('heading', { name: '数据 Schema' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Robot Joint State' })).toBeVisible();
});
