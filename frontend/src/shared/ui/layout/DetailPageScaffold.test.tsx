// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';
import { DetailPageScaffold } from './DetailPageScaffold';

afterEach(() => cleanup());

describe('DetailPageScaffold', () => {
  const header = { title: '数据集详情' };

  it('uses the full content row when there is no inspector', () => {
    const { container } = render(
      <DetailPageScaffold header={header} resourceId="dataset-1">
        <p>详情内容</p>
      </DetailPageScaffold>,
    );

    expect(container.querySelector('[data-has-inspector]')).not.toBeInTheDocument();
    expect(screen.getByText('详情内容')).toBeVisible();
  });

  it('marks the grid when an inspector column is present', () => {
    const { container } = render(
      <DetailPageScaffold
        header={header}
        resourceId="dataset-1"
        inspector={<p>检查器内容</p>}
      >
        <p>详情内容</p>
      </DetailPageScaffold>,
    );

    expect(container.querySelector('[data-has-inspector="true"]')).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '详情检查器' })).toBeVisible();
  });
});
