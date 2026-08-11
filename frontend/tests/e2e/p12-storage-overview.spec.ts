import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;

test.describe('P12 存储容量', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(/\/src\/mocks\/handlers\/(?!storage-overview\.handlers\.ts)[^/]+\.handlers\.ts/u, (route) => route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }));
  });

  test('主流程：只读概览、对象详情、Multipart、费用与四档截图', async ({ page }) => {
    await page.goto('/storage/overview?mockScenario=storage-overview:happy');
    await expect(page.getByRole('heading', { name: '存储容量' })).toBeVisible();
    await expect(page.getByRole('region', { name: '存储指标' })).toContainText('实际 OSS 容量');

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      const screenshot = await page.screenshot({ fullPage: true, animations: 'disabled' });
      expect(screenshot.byteLength).toBeGreaterThan(1_000);
    }

    await page.getByRole('button', { name: 'Inventory 对象' }).click();
    await expect(page.getByRole('table', { name: '同一快照下的存储对象事实' })).toBeVisible();
    await page.getByRole('button', { name: 'source/•••/02' }).click();
    await expect(page.getByRole('dialog', { name: '对象详情' })).toContainText('只读');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: '对象详情' })).toBeHidden();

    await page.getByRole('button', { name: 'Multipart 诊断' }).click();
    await expect(page.getByRole('table', { name: '只读 Multipart 上传诊断' })).toContainText('multipart_fx_01');
    await page.getByRole('button', { name: '费用' }).click();
    await expect(page.getByRole('heading', { name: '2026-08 费用构成' })).toBeVisible();
    await expect(page.getByRole('main').first()).not.toContainText(/执行|删除|Restore|Abort/u);
  });

  test('失败流程：非法 bytes 合同安全阻断', async ({ page }) => {
    await page.goto('/storage/overview?mockScenario=storage-overview:contract-mismatch');
    await expect(page.getByText(/存储响应不符合合同/)).toBeVisible();
    await expect(page.locator('body')).not.toContainText('2147483648 B');
  });
});
