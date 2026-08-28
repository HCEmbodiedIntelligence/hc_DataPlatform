// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';
import { StatusTag } from './StatusTag';

afterEach(() => cleanup());

describe('StatusTag', () => {
  it('keeps an explicit unknown-state label without marking the value as known', () => {
    render(<StatusTag status="UNKNOWN" label="复核结果未知" known={false} />);

    const tag = screen.getByLabelText('状态：复核结果未知');
    expect(tag).toHaveTextContent('复核结果未知');
    expect(tag).toHaveAttribute('data-known', 'false');
  });
});
