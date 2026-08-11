import { expect, test } from '@playwright/test';

test('P16 标定管理主流程可渲染', async ({ page }) => {
  await page.goto('/settings/calibrations?mockScenario=management:happy');
  await expect(page.getByRole('heading', { name: '标定管理' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'calibration_set_fx_01' })).toBeVisible();
});
