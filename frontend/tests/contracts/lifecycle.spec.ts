import { describe, expect, it } from 'vitest';
import { lifecyclePageWireSchema } from '../../src/features/lifecycle/api';
import { lifecyclePageFixture } from '../../src/mocks/fixtures/management';
import { storageLifecycleQueryCodec } from '../../src/pages/p13-storage-lifecycle/query-codec';

describe('P13 lifecycle contract', () => {
  it('accepts the frozen lifecycle page fixture', () => {
    expect(lifecyclePageWireSchema.parse(lifecyclePageFixture).data.policy_set_version).toBe('3');
  });

  it('round-trips the canonical page query', () => {
    const search = { tab: 'policies' as const, policyId: 'policy_fx_01', intent: 'simulate' as const };
    const canonical = storageLifecycleQueryCodec.parse(storageLifecycleQueryCodec.build(search));
    expect(storageLifecycleQueryCodec.parse(storageLifecycleQueryCodec.build(canonical))).toEqual(canonical);
  });
});
