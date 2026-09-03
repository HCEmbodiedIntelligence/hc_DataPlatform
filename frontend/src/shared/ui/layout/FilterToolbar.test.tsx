// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilterToolbar } from './FilterToolbar';

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(() => cleanup());

describe('FilterToolbar', () => {
  it('keeps filters accessible while providing a compact collapsed summary', async () => {
    const user = userEvent.setup();
    render(
      <FilterToolbar
        label="数据源筛选"
        collapsible
        activeFilterCount={2}
        collapsedSummary="机器人 · 在线 · 最近更新 · 20 条/页"
      >
        <label>
          搜索
          <input aria-label="数据源名称" />
        </label>
      </FilterToolbar>,
    );

    const collapse = screen.getByRole('button', { name: '收起' });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('textbox', { name: '数据源名称' })).toBeVisible();

    await user.click(collapse);

    const expand = screen.getByRole('button', { name: '展开' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('机器人 · 在线 · 最近更新 · 20 条/页')).toBeVisible();
    expect(
      screen.getByRole('textbox', { name: '数据源名称', hidden: true }),
    ).not.toBeVisible();

    await user.click(expand);
    expect(screen.getByRole('textbox', { name: '数据源名称' })).toBeVisible();
  });

  it('can collapse after applying filters', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <FilterToolbar collapsible collapseOnApply onApply={onApply}>
        <input aria-label="关键词" />
      </FilterToolbar>,
    );

    await user.click(screen.getByRole('button', { name: '应用筛选' }));

    expect(onApply).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: '展开' })).toHaveAttribute('aria-expanded', 'false');
  });
});
