import { describe, expect, it, vi } from 'vitest';
import { defineQueryCodec, registerPageRoutes, safeReturnTo } from '../../src/shared/routing/route-registry';

describe('shared routing contracts', () => {
  it('rejects external, protocol-relative and unregistered returnTo values', () => {
    registerPageRoutes('PX1', ['/fixture/:id']);
    expect(safeReturnTo('/fixture/resource_fx_01?tab=details')).toBe('/fixture/resource_fx_01?tab=details');
    expect(safeReturnTo('https://evil.invalid/fixture/resource_fx_01')).toBeNull();
    expect(safeReturnTo('//evil.invalid/fixture/resource_fx_01')).toBeNull();
    expect(safeReturnTo('/not-registered')).toBeNull();
  });

  it('round-trips build -> parse -> normalize -> build and resets cursors on filter changes', () => {
    const diagnostic = vi.fn();
    const codec = defineQueryCodec({
      defaults: { q: '', sort: 'updatedAt:desc', limit: 50, after: undefined as string | undefined, before: undefined as string | undefined },
      allowedKeys: ['q', 'sort', 'limit', 'after', 'before'],
      fields: { limit: { parse: (raw) => Number(raw) } },
      cursorResetKeys: ['q', 'sort', 'limit'],
      onDiagnostic: diagnostic,
    });
    const first = codec.build({ q: 'robot', sort: 'updatedAt:desc', limit: 50, after: 'cursor_fx_01' });
    const parsed = codec.parse(first);
    const normalized = codec.normalize(parsed);
    expect(codec.build(normalized)).toBe(first);

    const changed = codec.normalize({ ...parsed, q: 'arm' }, parsed);
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();

    codec.parse('q=robot&dangerous=%3Cscript%3Esecret%3C%2Fscript%3E');
    expect(diagnostic).toHaveBeenCalledWith({ code: 'UNKNOWN_QUERY_KEY', key: 'dangerous' });
    expect(JSON.stringify(diagnostic.mock.calls)).not.toContain('<script>');
  });

  it('clears cursors for optional custom-codec filters that are not present in defaults', () => {
    interface SearchState {
      q?: string;
      limit: number;
      after?: string;
      before?: string;
    }
    const codec = defineQueryCodec<SearchState>({
      defaults: { limit: 50 },
      parse: (params) => ({
        limit: Number(params.get('limit') ?? 50),
        ...(params.get('q') ? { q: params.get('q') ?? undefined } : {}),
        ...(params.get('after') ? { after: params.get('after') ?? undefined } : {}),
        ...(params.get('before') ? { before: params.get('before') ?? undefined } : {}),
      }),
      build: (value) => {
        const params = new URLSearchParams();
        if (value.q) params.set('q', value.q);
        if (value.after) params.set('after', value.after);
        if (value.before) params.set('before', value.before);
        return params;
      },
    });
    const current: SearchState = { q: 'robot', limit: 50, after: 'cursor_fx_01' };
    const changed = codec.normalize({ ...current, q: 'arm' }, current);
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();
  });
});
