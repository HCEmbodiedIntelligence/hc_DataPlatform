import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button, Input } from 'antd';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProviderHarness } from '../../src/app/providers';
import {
  DetailPageScaffold,
  EntityDrawer,
  FilterToolbar,
  StandardPageScaffold,
  WorkbenchScaffold,
} from '../../src/shared/ui/layout';
import { MetricCard, PAGE_STATE_KINDS, PageState, StatusTag } from '../../src/shared/ui/state';

const originalMatchMedia = window.matchMedia;
const layoutCss = readFileSync(
  resolve(process.cwd(), 'src/shared/ui/layout/layout.module.css'),
  'utf8',
);
const stateCss = readFileSync(
  resolve(process.cwd(), 'src/shared/ui/state/state.module.css'),
  'utf8',
);

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string): MediaQueryList => {
      const max = /max-width:\s*(\d+)px/.exec(query)?.[1];
      const min = /min-width:\s*(\d+)px/.exec(query)?.[1];
      const matches = (!max || width <= Number(max)) && (!min || width >= Number(min));
      return {
        matches,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      };
    }),
  });
}

function supportPseudoElementStyleReads() {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element));
}

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: originalMatchMedia });
});

function DrawerHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>打开对象详情</Button>
      <EntityDrawer open={open} title="对象详情" onClose={() => setOpen(false)}>
        <Button>详情操作</Button>
      </EntityDrawer>
    </>
  );
}

describe('layout and state UI contract', () => {
  it('provides the three semantic layouts without owning page business state', () => {
    const view = render(
      <ProviderHarness>
        <StandardPageScaffold
          header={{
            title: '标准列表',
            breadcrumbs: [
              { key: 'home', label: <a href="/">首页</a> },
              { key: 'list', label: '标准列表' },
            ],
            actions: <Button type="primary">新建</Button>,
          }}
          summary={<span>摘要</span>}
          filters={<span>筛选</span>}
          pagination={<Button>下一组</Button>}
        >
          <span>列表内容</span>
        </StandardPageScaffold>
        <DetailPageScaffold
          header={{ title: '固定版本详情' }}
          resourceId="version_01HXYZ"
          tabs={<Button>概览</Button>}
          inspector={<span>只读检查器</span>}
        >
          <span>详情内容</span>
        </DetailPageScaffold>
        <WorkbenchScaffold
          header={{ title: '编辑工作台' }}
          navigation={<span>队列</span>}
          toolbar={<Button>保存</Button>}
          media={<span>媒体</span>}
          editor={<span>编辑器</span>}
          inspector={<span>检查器</span>}
          timeline={<span>时间轴内容</span>}
        />
      </ProviderHarness>,
    );

    expect(view.container.querySelectorAll('[data-layout="standard-page"]')).toHaveLength(1);
    expect(view.container.querySelectorAll('[data-layout="detail-page"]')).toHaveLength(1);
    expect(view.container.querySelectorAll('[data-layout="workbench"]')).toHaveLength(1);
    expect(screen.getByRole('navigation', { name: '面包屑' })).toBeInTheDocument();
    expect(screen.getByText('不可变资源 ID：')).toBeInTheDocument();
    expect(screen.getByText('version_01HXYZ')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '媒体工作区' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '编辑工作区' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '时间轴' })).toBeInTheDocument();
  });

  it('locks desktop, tablet and compact layout behavior in scoped styles', () => {
    expect(layoutCss).toContain('grid-template-areas');
    expect(layoutCss).toContain('@media (max-width: 1024px)');
    expect(layoutCss).toContain('@media (max-width: 768px)');
    expect(layoutCss).toContain('@media (max-width: 390px)');
    expect(stateCss).toContain('@media (max-width: 768px)');
    expect(stateCss).toContain('@media (max-width: 390px)');
    expect(layoutCss).not.toMatch(
      /(^|\})\s*\.(page-header|filter-bar|metric-grid|dialog-actions)\s*\{/m,
    );
  });

  it('renders the complete fail-closed page-state matrix with distinct 403, 404, 410 and 429 states', () => {
    const expected = new Map([
      ['empty', '暂无数据'],
      ['filtered-empty', '当前筛选没有结果'],
      ['error', '此区域暂时不可用'],
      ['forbidden', '无权访问'],
      ['not-found', '资源不存在'],
      ['gone', '资源已失效'],
      ['conflict', '服务器事实已变化'],
      ['rate-limited', '请求频率受限'],
      ['offline', '网络连接中断'],
      ['contract-mismatch', '数据合同不匹配'],
      ['unknown', '发现未知状态'],
      ['feature-unavailable', '能力尚未开放'],
    ] as const);

    expect(PAGE_STATE_KINDS).toEqual([
      'loading',
      'refreshing',
      'empty',
      'filtered-empty',
      'error',
      'forbidden',
      'not-found',
      'gone',
      'conflict',
      'rate-limited',
      'offline',
      'contract-mismatch',
      'unknown',
      'feature-unavailable',
    ]);

    for (const [state, copy] of expected) {
      const view = render(
        <ProviderHarness>
          <PageState state={state} label={`${state}状态`} action={<Button>危险写操作</Button>} />
        </ProviderHarness>,
      );
      expect(view.container).toHaveTextContent(copy);
      expect(view.container.querySelector(`[data-page-state="${state}"]`)).toBeInTheDocument();
      if (state === 'unknown' || state === 'contract-mismatch' || state === 'feature-unavailable') {
        expect(view.container.querySelector('[data-safe-mode="read-only"]')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: '危险写操作' })).not.toBeInTheDocument();
      }
      view.unmount();
    }

    const loading = render(
      <ProviderHarness>
        <PageState state="loading" label="列表" />
      </ProviderHarness>,
    );
    expect(loading.container.querySelector('[data-page-state="loading"]')).toHaveAttribute(
      'aria-busy',
      'true',
    );
    loading.unmount();

    render(
      <ProviderHarness>
        <PageState state="refreshing" label="列表">
          <span>上次成功数据</span>
        </PageState>
      </ProviderHarness>,
    );
    expect(screen.getByText('上次成功数据')).toBeInTheDocument();
    expect(screen.getByText('正在刷新')).toBeInTheDocument();
  });

  it('uses text, icon and tone for status while keeping UNKNOWN and metric precision safe', () => {
    render(
      <ProviderHarness>
        <StatusTag status="PUBLISHED" label="已发布" tone="success" />
        <StatusTag status="UNKNOWN" label="错误放行文案" tone="success" />
        <MetricCard
          label="逻辑字节"
          value="900719925474099312345"
          unit="bytes"
          basis="逻辑口径"
          asOf="2026-08-12T11:00:00Z"
        />
        <MetricCard label="受限指标" value="999" state="forbidden" />
        <MetricCard label="未知指标" value="999" state="unknown" />
      </ProviderHarness>,
    );

    expect(screen.getByLabelText('状态：已发布')).toHaveAttribute('data-known', 'true');
    expect(screen.getByLabelText('状态：未知状态')).toHaveAttribute('data-known', 'false');
    expect(screen.queryByText('错误放行文案')).not.toBeInTheDocument();
    expect(screen.getByText('900719925474099312345')).toBeInTheDocument();
    expect(screen.getByLabelText('受限指标')).toHaveTextContent('无权查看');
    expect(screen.getByLabelText('未知指标')).toHaveTextContent('未知');
    expect(screen.queryByText('999')).not.toBeInTheDocument();
  });

  it('uses a single compact filter form in a Drawer and restores focus after applying', async () => {
    setViewportWidth(390);
    supportPseudoElementStyleReads();
    const onApply = vi.fn();
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <FilterToolbar onApply={onApply} onReset={vi.fn()}>
          <label>
            名称
            <Input aria-label="名称" />
          </label>
        </FilterToolbar>
      </ProviderHarness>,
    );

    const trigger = screen.getByRole('button', { name: /筛选/ });
    await user.click(trigger);
    expect(await screen.findByRole('dialog', { name: '筛选条件' })).toBeInTheDocument();
    expect(screen.getAllByLabelText('名称')).toHaveLength(1);
    await user.type(screen.getByLabelText('名称'), 'dataset');
    await user.click(screen.getByRole('button', { name: '应用筛选' }));
    expect(onApply).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: '筛选条件' })).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  });

  it('closes EntityDrawer with Escape and returns focus to the trigger', async () => {
    setViewportWidth(390);
    supportPseudoElementStyleReads();
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <DrawerHarness />
      </ProviderHarness>,
    );

    const trigger = screen.getByRole('button', { name: '打开对象详情' });
    await user.click(trigger);
    expect(await screen.findByRole('dialog', { name: '对象详情' })).toBeInTheDocument();
    expect(
      screen.getByRole('dialog', { name: '对象详情' }).closest('.ant-drawer-content-wrapper'),
    ).toHaveStyle({ width: '100%' });
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: '对象详情' })).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  });
});
