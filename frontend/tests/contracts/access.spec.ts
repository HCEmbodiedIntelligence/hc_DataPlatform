import { describe, expect, it } from 'vitest';
import { accessBootstrapWireSchema, membersPageWireSchema } from '../../src/features/access/api';
import { capabilityCatalog } from '../../src/features/access/capability-catalog';
import { accessBootstrapFixture, membersPageFixture } from '../../src/mocks/fixtures/management';
import { accessQueryCodec } from '../../src/pages/p18-access/query-codec';

describe('P18 access contract', () => {
  it('accepts bootstrap/member fixtures and exact role ceilings', () => {
    expect(accessBootstrapWireSchema.parse(accessBootstrapFixture).data.summary.active_members).toBe('3');
    expect(membersPageWireSchema.parse(membersPageFixture).items[0]?.role_id).toBe('PROJECT_ADMIN');
    expect(capabilityCatalog.canonical.size).toBe(76);
    expect(capabilityCatalog.roleCeilings.PROJECT_DEVELOPER.size).toBe(34);
    expect(capabilityCatalog.roleCeilings.PROJECT_DATA_PROCESSOR.size).toBe(19);
  });

  it('round-trips stable member selection', () => {
    const search = { tab: 'members' as const, memberId: 'membership_fx_admin', q: 'Fixture' };
    const canonical = accessQueryCodec.parse(accessQueryCodec.build(search));
    expect(accessQueryCodec.parse(accessQueryCodec.build(canonical))).toEqual(canonical);
  });
});
