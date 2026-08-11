import { expect, test } from '@playwright/test';

const widths = [1440, 1024, 768, 390] as const;

test.describe('P08 数据标注', () => {
  test('主流程：队列、任务工作台、四档截图和键盘时间轴', async ({ page }, testInfo) => {
    await page.goto('/annotations?mockScenario=annotation:happy');
    await expect(page.getByRole('heading', { name: '数据标注', level: 1 })).toBeVisible();
    await expect(page.getByText('ann-task-progress-01')).toBeVisible();
    await page.getByRole('button', { name: 'ann-task-progress-01' }).click();
    await expect(page.getByRole('heading', { name: /任务 ann-task-progress-01/ })).toBeVisible();
    const timeline = page.getByRole('slider', { name: 'Episode 时间轴' });
    await timeline.focus();
    await timeline.press('ArrowRight');
    await timeline.press('Home');
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.evaluate(() => scrollTo(0, 0));
      const screenshot = await page.screenshot({ fullPage: true });
      expect(screenshot.byteLength).toBeGreaterThan(1_000);
      await testInfo.attach(`p08-workbench-${width}`, { body: screenshot, contentType: 'image/png' });
    }
    await page.setViewportSize({ width: 720, height: 450 });
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    await page.evaluate(() => scrollTo(0, 0));
    const zoomedScreenshot = await page.screenshot({ fullPage: true });
    expect(zoomedScreenshot.byteLength).toBeGreaterThan(1_000);
    await testInfo.attach('p08-workbench-200-percent', { body: zoomedScreenshot, contentType: 'image/png' });
  });

  test('主流程：保存、提交并通过不可变复核', async ({ page }) => {
    await page.goto('/annotations/tasks/ann-task-progress-01?mockScenario=annotation:happy');
    const label = page.getByLabel(/^标签/);
    await expect(label).toHaveValue('GRASP_PART');
    await label.fill('GRASP_PART_UPDATED');
    await page.getByRole('button', { name: '应用到工作副本' }).click();

    const save = page.getByRole('button', { name: '保存标注' });
    await expect(save).toBeEnabled();
    await save.click();
    await expect(save).toBeDisabled();

    await page.getByRole('button', { name: '提交复核' }).click();
    await expect(page.getByRole('dialog', { name: '确认提交标注复核' })).toBeVisible();
    await page.getByRole('button', { name: '确认提交' }).click();
    await expect(page.getByRole('button', { name: '通过标注' })).toBeVisible();

    await page.getByRole('button', { name: '通过标注' }).click();
    await expect(page.getByRole('dialog', { name: '确认通过标注' })).toBeVisible();
    await page.getByRole('button', { name: '确认通过' }).click();
    await expect(page.getByText('COMPLETED', { exact: true })).toBeVisible();
  });

  test('失败流程：STALE 不保存、不静默迁移且只显示显式 Rebase', async ({ page }) => {
    await page.goto('/annotations/tasks/ann-task-stale-01?mockScenario=annotation:stale');
    await expect(page.getByText(/任务已 STALE/)).toBeVisible();
    await expect(page.getByRole('button', { name: '保存标注' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '在新修订上重建任务' })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('自动迁移成功');
  });

  test('失败流程：权限撤销后不泄露任务数据', async ({ page }) => {
    await page.goto('/annotations?mockScenario=annotation:permission-revoked');
    await expect(page.getByRole('heading', { name: '无权访问' })).toBeVisible();
    await expect(page.getByText(/页面未发起领域请求/)).toBeVisible();
    await expect(page.locator('body')).not.toContainText('ann-task-progress-01');
  });
});
