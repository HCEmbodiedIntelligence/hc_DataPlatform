import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;

test.describe('P01 数据工作台', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(/\/src\/mocks\/handlers\/(?!dashboard\.handlers\.ts)[^/]+\.handlers\.ts/u, (route) => route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }));
  });

  test('主流程：聚合指标、按需覆盖率、时间筛选与四档截图', async ({ page }) => {
    await page.goto('/dashboard?mockScenario=dashboard:happy');
    await expect(page.getByRole('heading', { name: '数据工作台' })).toBeVisible();
    await expect(page.getByRole('region', { name: '关键指标' })).toContainText('期间上传量');
    await expect(page.getByRole('heading', { name: '待办与最近活动' })).toBeVisible();

    await page.getByRole('button', { name: '按需加载覆盖率矩阵' }).click();
    await expect(page.getByRole('table', { name: '机器人组与任务覆盖率' })).toBeVisible();

    await page.getByLabel('时间范围').selectOption('7d');
    await expect(page).toHaveURL(/range=7d/u);
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      const screenshot = await page.screenshot({ fullPage: true, animations: 'disabled' });
      expect(screenshot.byteLength).toBeGreaterThan(1_000);
    }
    await page.getByRole('button', { name: '查看全部待办' }).click();
    await expect(page.getByRole('dialog', { name: '全部待办' })).toContainText('上传任务失败');
    await page.keyboard.press('Escape');
  });

  test('失败流程：合同不匹配只阻断受影响区域', async ({ page }) => {
    await page.goto('/dashboard?mockScenario=dashboard:contract-mismatch');
    await expect(page.getByText(/服务端数据与页面合同不一致/).first()).toBeVisible();
    await expect(page.getByRole('heading', { name: '待办与最近活动' })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('1500 bytes');
  });
});
