import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;
test.describe('P02 数据源', () => {
  test('主流程：列表、详情、连接测试入口和四档视觉', async ({ page }) => {
    await page.goto('/ingest/sources?mockScenario=ingest:happy');
    await expect(page.getByRole('heading', { name: '数据源' })).toBeVisible();
    await page.getByRole('button', { name: /查看 上海采集站 A/u }).click();
    await expect(page.getByRole('complementary', { name: '数据源详情' })).toContainText('已配置');
    await expect(page.getByRole('button', { name: '编辑' })).toBeEnabled();
    await page.getByRole('button', { name: '测试连接' }).click();
    await expect(page.getByRole('status')).toHaveText('连接测试：QUEUED（polling）', { timeout: 15_000 });
    for (const width of widths) { await page.setViewportSize({ width, height: 900 }); await expect(page).toHaveScreenshot(`p02-happy-${width}.png`, { fullPage: true }); }
  });

  test('失败流程：合同不匹配安全阻断且不泄露秘密', async ({ page }) => {
    await page.goto('/ingest/sources?mockScenario=ingest:contract-mismatch');
    await expect(page.getByText(/响应未通过安全合同校验|页面加载失败/u)).toBeVisible();
    await expect(page.locator('body')).not.toContainText('fixture-only-leak');
  });
});
