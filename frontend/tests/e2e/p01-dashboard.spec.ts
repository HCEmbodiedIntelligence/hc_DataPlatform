import { expect, test, type Page } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;
const offModeBaseUrl = 'http://127.0.0.1:4174';
const dashboardAggregateEndpoints = ['activity', 'snapshot', 'coverage', 'pending-items'] as const;

function observeDashboardRequests(page: Page) {
  const requested = new Set<string>();
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    const endpoint = dashboardAggregateEndpoints.find((candidate) => pathname.endsWith(`/dashboard/${candidate}`));
    if (endpoint) requested.add(endpoint);
  });
  return requested;
}

test.describe('P01 数据工作台', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(/\/src\/mocks\/handlers\/(?!dashboard\.handlers\.ts)[^/]+\.handlers\.ts/u, (route) => route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }));
  });

  test('主流程：聚合指标、按需覆盖率、时间筛选与四档截图', async ({ page }) => {
    const dashboardRequests = observeDashboardRequests(page);
    await page.goto('/dashboard?mockScenario=dashboard:happy');
    await expect(page.getByRole('heading', { name: '数据工作台' })).toBeVisible();
    await expect(page.getByRole('region', { name: '关键指标' })).toContainText('期间上传量');
    await expect(page.getByRole('heading', { name: '待办与最近活动' })).toBeVisible();

    await page.getByRole('button', { name: '按需加载覆盖率矩阵' }).click();
    await expect(page.getByRole('table', { name: '机器人组与任务覆盖率' })).toBeVisible();
    expect([...dashboardRequests].sort()).toEqual([...dashboardAggregateEndpoints].sort());

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

  test('真实 API 模式：产品合同未定义时显式降级且四个聚合路径零请求', async ({ page }) => {
    const dashboardRequests = observeDashboardRequests(page);
    await page.goto(`${offModeBaseUrl}/dashboard`);

    const unavailable = page.getByRole('status', { name: '工作台聚合能力尚未开放' });
    await expect(unavailable).toBeVisible();
    await expect(unavailable).toContainText('产品合同尚未定义');
    await expect(unavailable).toContainText('不会展示模拟数据或占位指标');
    await expect(page.getByText('期间上传量')).toHaveCount(0);
    await page.waitForLoadState('networkidle');
    expect([...dashboardRequests]).toEqual([]);
  });

  test('失败流程：合同不匹配只阻断受影响区域', async ({ page }) => {
    await page.goto('/dashboard?mockScenario=dashboard:contract-mismatch');
    await expect(page.getByText(/服务端数据与页面合同不一致/).first()).toBeVisible();
    await expect(page.getByRole('heading', { name: '待办与最近活动' })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('1500 bytes');
  });
});
