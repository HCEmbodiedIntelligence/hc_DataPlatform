import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;
test.describe('P03 上传任务', () => {
  test('主流程：服务端列表、批量选择、详情 route builder 与四档视觉', async ({ page }) => {
    await page.goto('/ingest/uploads?mockScenario=ingest:happy');
    await expect(page.getByRole('heading', { name: '上传任务' })).toBeVisible();
    await page.getByRole('checkbox', { name: '选择 upload_fx_uploading' }).check();
    await expect(page.getByRole('region', { name: '批量操作' })).toContainText('已选择 1 项');
    await page.getByRole('button', { name: '批量暂停' }).click();
    await expect(page.getByRole('region', { name: '批量操作' })).toContainText('成功 1，失败 0');
    for (const width of widths) { await page.setViewportSize({ width, height: 900 }); await expect(page).toHaveScreenshot(`p03-happy-${width}.png`, { fullPage: true }); }
    await page.getByRole('button', { name: '查看详情' }).first().click();
    await expect(page).toHaveURL(/\/ingest\/uploads\/upload_fx_uploading/u);
  });

  test('失败流程：未知状态只读且不会被映射为失败', async ({ page }) => {
    await page.goto('/ingest/uploads?mockScenario=ingest:unknown-enum');
    await expect(page.getByText(/UNKNOWN \(FUTURE_TRANSFER\)/u)).toBeVisible();
  });
});
