import { describe, expect, it } from 'vitest';
import { lifecyclePolicyWireSchema } from './index';
import { cachePolicies } from '../../../pages/p13-storage-lifecycle/cache-policies';

describe('automatic cache policy contract', () => {
  for (const [action, config] of Object.entries(cachePolicies)) {
    it(`accepts ${action} and retains editable days`, () => {
      const policy = { policy_id: 'p', project_id: '01', name: config.label,
        business_category: 'PENDING_ANNOTATION', object_role: 'REBUILDABLE_DERIVATIVE',
        action, minimum_age_days: 14, priority: 100, state: 'ENABLED', version: 2,
        etag: 'etag', created_at: '2026-11-06T00:00:00Z', updated_at: '2026-11-06T00:00:00Z' };
      expect(lifecyclePolicyWireSchema.parse(policy).minimum_age_days).toBe(14);
      expect(lifecyclePolicyWireSchema.safeParse({...policy, object_role: 'RAW'}).success).toBe(false);
    });
  }
});
