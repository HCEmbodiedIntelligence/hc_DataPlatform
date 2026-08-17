// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';
import { PageHeader as LegacyPageHeader } from './PageHeader';
import { PageHeader } from './layout/PageHeader';

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

afterEach(cleanup);

describe('breadcrumb navigation', () => {
  it('navigates parent breadcrumbs without reloading and keeps the current item static', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/current']}>
        <PageHeader
          title="当前页"
          breadcrumbs={[
            { key: 'parent', label: '上级目录', to: '/parent' },
            { key: 'current', label: '当前页', to: '/should-not-link' },
          ]}
        />
        <LocationProbe />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '上级目录' }).getAttribute('href')).toBe('/parent');
    expect(screen.queryByRole('link', { name: '当前页' })).toBeNull();
    expect(screen.getByText('当前页', { selector: 'li' }).getAttribute('aria-current')).toBe(
      'page',
    );

    await user.click(screen.getByRole('link', { name: '上级目录' }));
    expect(screen.getByTestId('location').textContent).toBe('/parent');
  });

  it('uses client-side navigation for the legacy header href contract', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/workbench']}>
        <LegacyPageHeader
          title="工作台"
          breadcrumbs={[
            { label: '草稿列表', href: '/manual/drafts' },
            { label: '工作台' },
          ]}
        />
        <LocationProbe />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('link', { name: '草稿列表' }));
    expect(screen.getByTestId('location').textContent).toBe('/manual/drafts');
  });
});
