import { expect, test, type Page, type TestInfo } from '@playwright/test';
const widths = [1440, 1024, 768, 390] as const;

async function attachResponsiveScreenshots(page: Page, testInfo: TestInfo) {
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    await testInfo.attach(`p06-main-${width}.png`, {
      body: await page.screenshot({ fullPage: true }),
      contentType: 'image/png',
    });
  }
}
test.describe('P06 数据集详情', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(
      /\/src\/mocks\/handlers\/(?!datasets\.handlers\.ts)[^/]+\.handlers\.ts/u,
      (route) =>
        route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }),
    );
  });
  test('主流程：六个区域、固定版本、只读 Viewer 与四档截图', async ({ page }, testInfo) => {
    await page.goto('/datasets/dataset_fx_01?mockScenario=datasets:happy');
    await expect(page.getByRole('heading', { name: 'Assembly dataset' })).toBeVisible();
    await attachResponsiveScreenshots(page, testInfo);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('tab', { name: 'Schema' }).click();
    await expect(page.getByText('schema_snapshot_fx_01')).toBeVisible();
    await page.getByRole('tab', { name: '容量' }).click();
    await expect(page.getByRole('heading', { name: '容量事实' })).toBeVisible();
    await expect(page.getByText('SETTLED')).toBeVisible();
    await page.getByRole('tab', { name: 'Episodes' }).click();
    await page.getByRole('button', { name: /#1/ }).click();
    await expect(page.getByRole('heading', { name: 'Episode Inspector' })).toBeVisible();
    await page.getByRole('button', { name: '打开只读 Viewer' }).click();
    await expect(page).toHaveURL(/versions\/version_fx_review_01\/episodes\/episode_fx_01\/view/);
    await expect(page.getByText('Readonly episode viewer')).toBeVisible();
    await expect(page.locator('[data-mode="readonly"]')).toBeVisible();
    await expect(page.getByRole('button', { name: /CleaningDraft|清洗草稿|打开 P11/ })).toHaveCount(
      0,
    );
  });
  test('失败流程：gone 与 not-found 不混淆', async ({ page }) => {
    await page.goto('/datasets/dataset_fx_01?mockScenario=datasets:gone');
    await expect(page.locator('.dataset-region-state strong')).toHaveText('资源已失效');
  });
});
