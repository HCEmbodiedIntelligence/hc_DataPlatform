import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { isDomainError } from '../../src/shared/api/domain-error';
import { makePageSchema } from '../../src/shared/api/pagination';
import { parseWire } from '../../src/shared/api/validate';

describe('wire contract validation', () => {
  it('throws CONTRACT_MISMATCH and logs pointers without leaking values', () => {
    const logger = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    let thrown: unknown;
    try {
      parseWire(z.object({ user: z.object({ id: z.string() }) }), { user: { id: 42, secret: 'DO_NOT_LOG' } }, {
        endpoint: 'https://fixture.invalid/object?X-Amz-Signature=DO_NOT_LOG_SIGNATURE',
        schemaVersion: 'fixture.v1',
        requestId: 'req_fx_contract',
      });
    } catch (error) {
      thrown = error;
    }
    expect(isDomainError(thrown)).toBe(true);
    if (isDomainError(thrown)) expect(thrown.code).toBe('CONTRACT_MISMATCH');
    const logText = JSON.stringify(logger.mock.calls);
    expect(logText).toContain('/user/id');
    expect(logText).toContain('req_fx_contract');
    expect(logText).not.toContain('DO_NOT_LOG');
    expect(logText).not.toContain('DO_NOT_LOG_SIGNATURE');
    expect(logText).not.toContain('42');
  });

  it('validates the complete cursor page_info wire shape', () => {
    const schema = makePageSchema(z.object({ id: z.string() }));
    const valid = {
      items: [{ id: 'dataset_fx_01' }],
      page_info: {
        has_next_page: true,
        has_previous_page: false,
        start_cursor: 'cursor_fx_start',
        end_cursor: 'cursor_fx_end',
      },
      snapshot_at: '2026-08-05T08:00:00Z',
    };
    expect(schema.safeParse(valid).success).toBe(true);
    expect(schema.safeParse({ ...valid, page_info: { has_next_page: true } }).success).toBe(false);
    expect(schema.safeParse({ ...valid, page_info: { ...valid.page_info, has_next_page: 'yes' } }).success).toBe(false);
  });
});
