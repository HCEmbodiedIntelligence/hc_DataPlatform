import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;

test.describe('P19 审计日志', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(/\/src\/mocks\/handlers\/(?!audit\.handlers\.ts)[^/]+\.handlers\.ts/u, (route) => route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }));
  });

  test('主流程：筛选、详情、能力字段与四档截图', async ({ page }) => {
    await page.goto('/settings/audit?mockScenario=audit:admin-view');
    await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible();
    await expect(page.getByRole('table', { name: /稳定排序/ })).toContainText('access.membership.role_changed');

    await page.getByLabel('风险').selectOption('HIGH');
    await expect(page).toHaveURL(/riskLevel=HIGH/u);
    await page.getByRole('row', { name: /access.membership.role_changed/ }).click();
    await expect(page.getByRole('dialog', { name: '事件详情' })).toContainText('完整性证据');

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      const screenshot = await page.screenshot({ fullPage: true, animations: 'disabled' });
      expect(screenshot.byteLength).toBeGreaterThan(1_000);
    }
  });

  test('失败流程：危险审计字段触发合同不匹配且不泄露', async ({ page }) => {
    await page.goto('/settings/audit?mockScenario=audit:contract-mismatch');
    await expect(page.getByText(/审计投影不符合合同/)).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('body')).not.toContainText('authorization_token');
    await expect(page.locator('body')).not.toContainText('must-be-rejected');
  });

  test('三种读取投影由 capability 决定', async ({ page }) => {
    await page.goto('/settings/audit?mockScenario=audit:developer-view&eventId=audit_event_fx_02');
    await expect(page.getByRole('dialog', { name: '事件详情' })).toContainText('安全变更摘要');
    await expect(page.getByRole('dialog', { name: '事件详情' })).not.toContainText('角色投影');
    await expect(page.getByRole('dialog', { name: '事件详情' })).not.toContainText('完整性证据');

    await page.goto('/settings/audit?mockScenario=audit:processor-view&eventId=audit_event_fx_02');
    await expect(page.getByRole('dialog', { name: '事件详情' })).toContainText('未提供或不适用');
    await expect(page.getByRole('dialog', { name: '事件详情' })).not.toContainText('请求上下文');
  });
});
