import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button, Empty, theme } from 'antd';
import { createMemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AppProviders, ProviderHarness } from '../../src/app/providers';
import { useToast } from '../../src/app/providers/ToastProvider';
import { uiTheme } from '../../src/app/theme/component-theme';
import { platformTokens } from '../../src/app/theme/tokens';

function ThemeAndLocaleProbe() {
  const { token } = theme.useToken();
  return (
    <>
      <output
        data-testid="theme-probe"
        data-primary={token.colorPrimary}
        data-page={token.colorBgLayout}
        data-radius={token.borderRadiusLG}
      />
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
    </>
  );
}

function ToastProbe({ sensitive = false }: { sensitive?: boolean }) {
  const { showToast } = useToast();
  return (
    <Button
      onClick={() =>
        showToast({
          title: sensitive ? '导出地址' : '保存成功',
          message: sensitive ? 'oss://private-bucket/team-a/export.csv' : '变更已保存。',
          tone: sensitive ? 'warning' : 'success',
        })
      }
    >
      {sensitive ? '显示敏感通知' : '显示通知'}
    </Button>
  );
}

describe('UI provider contract', () => {
  it('defines the bounded semantic theme and compact component defaults', () => {
    expect(platformTokens.spacing).toEqual({ xs: 4, sm: 8, md: 12, lg: 16, xl: 24 });
    expect(Math.max(...Object.values(platformTokens.radius))).toBeLessThanOrEqual(8);
    expect(platformTokens.breakpoint).toEqual({ compact: 390, tablet: 768, desktop: 1024, wide: 1440 });
    expect(uiTheme.token).toMatchObject({
      colorPrimary: platformTokens.color.primary,
      colorPrimaryHover: platformTokens.color.primaryHover,
      colorPrimaryActive: platformTokens.color.primaryActive,
      colorBgLayout: platformTokens.color.page,
      colorBgContainer: platformTokens.color.surface,
      colorBorder: platformTokens.color.border,
      colorText: platformTokens.color.text,
      colorSuccess: platformTokens.color.success,
      colorWarning: platformTokens.color.warning,
      colorError: platformTokens.color.error,
      colorInfo: platformTokens.color.info,
      controlHeight: 36,
      controlHeightSM: 28,
      zIndexPopupBase: platformTokens.zIndex.popup,
    });
    expect(uiTheme.components).toMatchObject({
      Input: { borderRadius: 6, controlHeight: 36 },
      Select: { borderRadius: 6, controlHeight: 36, optionHeight: 32 },
      Table: { cellPaddingBlock: 8, cellPaddingInline: 12, headerBorderRadius: 8 },
      Modal: { borderRadiusLG: 8 },
      Notification: { zIndexPopup: platformTokens.zIndex.toast },
    });
  });

  it('uses one Chinese UI provider in both the harness and production stack', async () => {
    const harness = render(
      <ProviderHarness>
        <ThemeAndLocaleProbe />
      </ProviderHarness>,
    );
    const harnessProbe = await harness.findByTestId('theme-probe');
    const harnessContract = {
      primary: harnessProbe.dataset.primary,
      page: harnessProbe.dataset.page,
      radius: harnessProbe.dataset.radius,
    };
    expect(harness.getByText('暂无数据', { selector: '.ant-empty-description' })).toBeInTheDocument();
    expect(harness.container.querySelectorAll('.ant-app')).toHaveLength(1);
    harness.unmount();

    const router = createMemoryRouter([{ path: '/', element: <ThemeAndLocaleProbe /> }]);
    const production = render(<AppProviders router={router} />);
    const productionProbe = await production.findByTestId('theme-probe');
    expect({
      primary: productionProbe.dataset.primary,
      page: productionProbe.dataset.page,
      radius: productionProbe.dataset.radius,
    }).toEqual(harnessContract);
    expect(production.getByText('暂无数据', { selector: '.ant-empty-description' })).toBeInTheDocument();
    expect(production.container.querySelectorAll('.ant-app')).toHaveLength(1);
  });

  it('routes toast calls through an Ant Design portal without stealing focus', async () => {
    const user = userEvent.setup();
    const view = render(
      <ProviderHarness>
        <ToastProbe />
      </ProviderHarness>,
    );
    const trigger = screen.getByRole('button', { name: '显示通知' });
    await user.click(trigger);

    const title = await screen.findByText('保存成功');
    expect(screen.getByText('变更已保存。')).toBeInTheDocument();
    expect(view.container).not.toContainElement(title);
    expect(title.closest('.ant-notification-notice')).toHaveAttribute('role', 'status');
    expect(trigger).toHaveFocus();
  });

  it('does not expose secrets or complete locators in toast content', async () => {
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <ToastProbe sensitive />
      </ProviderHarness>,
    );
    await user.click(screen.getByRole('button', { name: '显示敏感通知' }));

    expect(await screen.findByText('敏感详情已隐藏。请通过受控详情页查看。')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('oss://private-bucket/team-a/export.csv')).not.toBeInTheDocument();
    });
  });
});
