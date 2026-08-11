import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;
test.describe('P04 上传详情', () => {
  test('主流程：概要、对象、流水线、隔离、重试和审计摘要', async ({ page }) => {
    await page.goto('/ingest/uploads/upload_fx_quarantined?mockScenario=ingest:happy');
    for (const heading of ['概要', '对象清单', '校验流水线阶段', '隔离区', '重试历史', '审计摘要']) await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible();
    await expect(page.getByText('仅为 Multipart 标识，不等价于内容摘要')).toBeVisible();
    await expect(page.getByText('内容完整性 SHA-256')).toBeVisible();
    for (const width of widths) { await page.setViewportSize({ width, height: 1100 }); await expect(page).toHaveScreenshot(`p04-happy-${width}.png`, { fullPage: true }); }
    await page.getByRole('button', { name: '复验并申请释放' }).click();
    await expect(page.getByText(/稳定 Upload ID/u)).toContainText('upload_fx_quarantined');
    await expect(page.getByText(/影响摘要/u)).toContainText('VerificationRun');
  });

  test('失败流程：连续授权过期/合同泄漏进入资源级错误', async ({ page }) => {
    await page.goto('/ingest/uploads/upload_fx_quarantined?mockScenario=ingest:contract-mismatch');
    await expect(page.getByText(/响应未通过安全合同校验|页面加载失败/u)).toBeVisible();
    await expect(page.locator('body')).not.toContainText('fixture.invalid/leak');
  });

  test('禁止 latest/current 路由身份', async ({ page }) => {
    await page.goto('/ingest/uploads/latest?mockScenario=ingest:happy');
    await expect(page.getByText(/资源不存在/u)).toBeVisible();
  });
});
