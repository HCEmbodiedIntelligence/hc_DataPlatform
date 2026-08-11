import { expect, test, type Page, type TestInfo } from '@playwright/test';
const widths = [1440, 1024, 768, 390] as const;

async function attachResponsiveScreenshots(page: Page, testInfo: TestInfo) {
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    await testInfo.attach(`p07-main-${width}.png`, {
      body: await page.screenshot({ fullPage: true }),
      contentType: 'image/png',
    });
  }
}
test.describe('P07 Version 详情', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(
      /\/src\/mocks\/handlers\/(?!datasets\.handlers\.ts)[^/]+\.handlers\.ts/u,
      (route) =>
        route.fulfill({ contentType: 'application/javascript', body: 'export default [];' }),
    );
  });
  test('主流程：固定 Version、事实区域、Review 预检与四档截图', async ({ page }, testInfo) => {
    await page.goto(
      '/datasets/dataset_fx_01/versions/version_fx_review_01?mockScenario=datasets:happy',
    );
    await expect(page.getByRole('heading', { name: 'v4' })).toBeVisible();
    await attachResponsiveScreenshots(page, testInfo);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('button', { name: 'revision_fx_01' }).click();
    await expect(page.getByRole('heading', { name: 'Revision Inspector' })).toBeVisible();
    await page.getByRole('tab', { name: 'Schema' }).click();
    await expect(page.getByText('channel_fx_rgb')).toBeVisible();
    await page.getByRole('tab', { name: '容量' }).click();
    await expect(page.getByText('object_fx_required_01')).toBeVisible();
    await expect(page.getByText('inventory_fx_01')).toBeVisible();
    await page.getByRole('tab', { name: 'Review' }).click();
    await page.getByRole('button', { name: '运行 Review 预检' }).click();
    await expect(page.getByText(/review-catalog-v3/)).toBeVisible();
  });
  test('失败流程：412 显示冲突并保留 Finding 输入', async ({ page }) => {
    await page.goto(
      '/datasets/dataset_fx_01/versions/version_fx_review_01?tab=review&mockScenario=datasets:etag-conflict',
    );
    await page.getByRole('button', { name: '运行 Review 预检' }).click();
    await page.getByRole('button', { name: '退回', exact: true }).click();
    await page.getByLabel('说明').fill('Camera occlusion must be cleaned again.');
    await page.getByRole('button', { name: '确认原子退回' }).click();
    await expect(page.getByText('检测到并发冲突')).toBeVisible();
    await expect(page.getByLabel('说明')).toHaveValue('Camera occlusion must be cleaned again.');
  });
  test('退回成功：留在 P07 核对原子事实，再只用 successor draftId 继续返工', async ({ page }) => {
    await page.goto(
      '/datasets/dataset_fx_01/versions/version_fx_review_01?tab=review&mockScenario=datasets:happy',
    );
    await page.getByRole('button', { name: '运行 Review 预检' }).click();
    await page.getByRole('button', { name: '退回', exact: true }).click();
    await page.getByLabel('说明').fill('Camera occlusion must be cleaned again.');
    const requestPromise = page.waitForRequest((request) =>
      request.url().endsWith('/versions/version_fx_review_01:return'),
    );
    await page.getByRole('button', { name: '确认原子退回' }).click();
    const request = await requestPromise;
    expect(request.headers()['if-match']).toBe('"version-review-rv-4"');
    expect(request.headers()['idempotency-key']).toBeTruthy();
    expect(await request.postDataJSON()).toMatchObject({
      expected_status: 'REVIEWING',
      finding_catalog_version: 'review-catalog-v3',
      findings: [
        {
          output_revision_id: 'revision_fx_01',
          episode_stream_id: 'stream_fx_cam_01',
        },
      ],
    });
    await expect(page).toHaveURL(
      /\/datasets\/dataset_fx_01\/versions\/version_fx_review_01\?tab=review/,
    );
    await expect(page.getByText('不可变退回事实')).toBeVisible();
    await page.getByRole('button', { name: '继续返工' }).click();
    await expect(page).toHaveURL(/\/manual\/drafts\/draft_fx_successor_01$/);
  });
  test('Review blockers 只阻断 Approve，Return 仍要求结构化 Finding', async ({ page }) => {
    await page.goto(
      '/datasets/dataset_fx_01/versions/version_fx_review_01?tab=review&mockScenario=datasets:preflight-blocked',
    );
    await page.getByRole('button', { name: '运行 Review 预检' }).click();
    await expect(page.getByRole('button', { name: '复核通过' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '退回', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '退回', exact: true }).click();
    await expect(page.getByText('ACTIVE_JOB：存在运行中的任务')).toBeVisible();
    await expect(page.getByRole('button', { name: '确认原子退回' })).toBeDisabled();
  });
});
