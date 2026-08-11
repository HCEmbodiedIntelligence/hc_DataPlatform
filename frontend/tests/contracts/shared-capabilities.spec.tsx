import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CANONICAL_CAPABILITIES, DATA_PROCESSOR_CAPABILITIES, DEVELOPER_CAPABILITIES } from '../../src/entities/capability';
import { CANONICAL_AUDIT_EVENTS } from '../../src/entities/audit-event';
import { makeScopeKey } from '../../src/entities/scope';
import { useCapabilities } from '../../src/shared/auth/use-capabilities';
import { useShellStore } from '../../src/shared/scope/shell-store';

describe('capability contracts', () => {
  it('matches the authoritative three-role snapshot counts', () => {
    expect(CANONICAL_CAPABILITIES).toHaveLength(76);
    expect(DEVELOPER_CAPABILITIES).toHaveLength(34);
    expect(DATA_PROCESSOR_CAPABILITIES).toHaveLength(19);
    expect(CANONICAL_AUDIT_EVENTS).toHaveLength(142);
    expect(new Set(CANONICAL_AUDIT_EVENTS).size).toBe(142);
  });

  it('fails closed for unknown capabilities and scope mismatch', () => {
    const scope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' };
    useShellStore.getState().setScope(scope);
    useShellStore.getState().setAuthorization({ scopeKey: makeScopeKey(scope), roleVersion: 'role_fx_01', capabilities: ['dataset.read'], fetchedAt: '2026-08-05T08:00:00Z' });
    const { result, rerender } = renderHook(() => useCapabilities());
    expect(result.current.has('dataset.read')).toBe(true);
    expect(result.current.has('future.unknown')).toBe(false);
    act(() => useShellStore.getState().setScope({ ...scope, projectId: 'prj_fx_02' }));
    rerender();
    expect(result.current.has('dataset.read')).toBe(false);
  });
});
