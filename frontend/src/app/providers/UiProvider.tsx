import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { ReactNode } from 'react';
import { uiTheme } from '../theme/component-theme';

export function UiProvider({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider locale={zhCN} theme={uiTheme} componentSize="middle">
      <AntApp
        notification={{
          placement: 'bottomRight',
          duration: 5,
          maxCount: 4,
          pauseOnHover: true,
        }}
      >
        {children}
      </AntApp>
    </ConfigProvider>
  );
}
