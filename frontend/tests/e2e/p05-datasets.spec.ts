import { expect, test, type Page, type TestInfo } from '@playwright/test';
const widths = [1440, 1024, 768, 390] as const;

async function chooseSelectOption(page: Page, label: string, option: string | RegExp) {
  await page.getByLabel(label).click();
  const dropdown = page.locator('.ant-select-dropdown:visible');
  await dropdown.locator('.ant-select-item-option').filter({ hasText: option }).click();
}

async function attachResponsiveScreenshots(page: Page, testInfo: TestInfo) {
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    await testInfo.attach(`p05-main-${width}.png`, {
      body: await page.screenshot({ fullPage: true }),
      contentType: 'image/png',
    });
  }
}
test.describe('P05 数据集列表', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(
      /\/src\/mocks\/handlers\/(?!datasets\.handlers\.ts)[^/]+\.handlers\.ts/u,
      (route) =>
        route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }),
    );
  });
  test('主流程：稳定列表、筛选、游标分页、详情跳转与四档截图', async ({ page }, testInfo) => {
    await page.goto('/datasets?mockScenario=datasets:cursor-pagination');
    await expect(page.getByRole('heading', { level: 1, name: '数据集' })).toBeVisible();
    await expect(page.getByText('dataset_fx_01')).toBeVisible();
    await attachResponsiveScreenshots(page, testInfo);
    await page.setViewportSize({ width: 1440, height: 900 });
    const requestPromise = page.waitForRequest(
      (request) =>
        request.method() === 'GET' &&
        new URL(request.url()).pathname.endsWith('/datasets') &&
        new URL(request.url()).searchParams.get('q') === 'assembly',
    );
    await page.getByLabel('搜索').fill('assembly');
    await chooseSelectOption(page, '机器人型号', /robot_model_fx_01/);
    await page.getByLabel('Channels（逗号分隔）').fill('/camera/front, /joint');
    await chooseSelectOption(page, 'Channel 匹配', '任一包含');
    await page.getByLabel('创建起始日').fill('2026-08-01');
    await page.getByLabel('创建结束日').fill('2026-08-11');
    await chooseSelectOption(page, '稳定排序', '名称（ID 升序兜底）');
    await chooseSelectOption(page, '每页', '50');
    await page.getByRole('button', { name: '应用筛选' }).click();
    const listRequest = await requestPromise;
    expect(new URL(listRequest.url()).searchParams.get('sort')).toBe('name:asc,dataset_id:asc');
    expect(new URL(listRequest.url()).searchParams.get('limit')).toBe('50');
    expect(new URL(listRequest.url()).searchParams.get('robot_model_id')).toBe('robot_model_fx_01');
    expect(new URL(listRequest.url()).searchParams.getAll('channels')).toEqual(['/camera/front', '/joint']);
    expect(new URL(listRequest.url()).searchParams.get('channel_match')).toBe('any');
    await page.getByRole('button', { name: '下一组' }).click();
    await expect(page).toHaveURL(/after=cursor_fx_next/);
    await expect(page.getByText('Waiting for ingest')).toBeVisible();
    await page.getByRole('button', { name: '上一组' }).click();
    await expect(page.getByText('dataset_fx_01')).toBeVisible();
    await page.getByRole('button', { name: /Assembly dataset/ }).click();
    await expect(page).toHaveURL(/\/datasets\/dataset_fx_01/);
  });
  test('失败流程：合同不匹配 fail closed', async ({ page }) => {
    await page.goto('/datasets?mockScenario=datasets:contract-mismatch');
    await expect(page.getByText('数据合同不匹配')).toBeVisible();
    await expect(page.locator('body')).not.toContainText('fixture.invalid/leak');
  });
});
