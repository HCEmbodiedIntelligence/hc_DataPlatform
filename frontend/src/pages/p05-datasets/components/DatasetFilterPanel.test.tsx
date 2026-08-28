// @vitest-environment jsdom

import { cleanup, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DatasetsSearch } from '../query-codec';
import { DatasetFilterPanel } from './DatasetFilterPanel';

const defaults: DatasetsSearch = {
  sort: 'activityDesc',
  limit: 20,
};

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
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('DatasetFilterPanel', () => {
  it('is collapsed by default and exposes an accessible expand/collapse button', async () => {
    const user = userEvent.setup();
    render(
      <DatasetFilterPanel search={defaults} onApply={vi.fn()} onReset={vi.fn()} />,
    );

    const toggle = screen.getByRole('button', { name: '展开' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls');
    expect(screen.queryByPlaceholderText('输入任务名称或 ID')).not.toBeInTheDocument();

    await user.click(toggle);

    expect(screen.getByRole('button', { name: '收起' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByPlaceholderText('输入任务名称或 ID')).toBeVisible();
  });

  it('has one task text input, no Channel controls, and all workflow options', async () => {
    const user = userEvent.setup();
    render(
      <DatasetFilterPanel search={defaults} onApply={vi.fn()} onReset={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getAllByText('检索任务')).toHaveLength(1);
    expect(screen.getAllByPlaceholderText('输入任务名称或 ID')).toHaveLength(1);
    expect(screen.queryByText(/Channels（逗号分隔）/)).not.toBeInTheDocument();
    expect(screen.queryByText('Channel 匹配')).not.toBeInTheDocument();

    const workflowField = screen.getByText('处理状态').closest('label');
    expect(workflowField).not.toBeNull();
    const workflowSelect = within(workflowField as HTMLLabelElement).getByRole('combobox');
    await user.click(workflowSelect);

    expect(await screen.findByText('待复核')).toBeInTheDocument();
    expect(screen.getByText('需返工')).toBeInTheDocument();
    expect(screen.getByText('有可处理草稿')).toBeInTheDocument();
  });

  it('applies a trimmed task and automatically collapses', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<DatasetFilterPanel search={defaults} onApply={onApply} onReset={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: '展开' }));
    await user.type(screen.getByPlaceholderText('输入任务名称或 ID'), '  pick  ');

    await user.click(screen.getByRole('button', { name: '应用筛选' }));

    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ task: 'pick' }));
    expect(screen.getByRole('button', { name: '展开' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(screen.queryByPlaceholderText('输入任务名称或 ID')).not.toBeInTheDocument();
  });

  it('shows the applied count without counting sort, page size, cursors, or collectionTaskId', () => {
    render(
      <DatasetFilterPanel
        search={{
          ...defaults,
          collectionTaskId: 'collection-1',
          q: 'assembly',
          task: 'pick',
          workflowState: 'pendingReview',
          datasetCreatedFrom: '2026-08-01',
          datasetCreatedTo: '2026-08-24',
          sort: 'nameAsc',
          limit: 100,
          after: 'cursor',
        }}
        onApply={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '筛选条件（4）' })).toBeVisible();
  });

  it('does not change the expanded state when reset is requested', async () => {
    const user = userEvent.setup();
    const onReset = vi.fn();
    render(<DatasetFilterPanel search={defaults} onApply={vi.fn()} onReset={onReset} />);
    await user.click(screen.getByRole('button', { name: '展开' }));

    await user.click(screen.getByRole('button', { name: '重置' }));

    expect(onReset).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: '收起' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });
});
