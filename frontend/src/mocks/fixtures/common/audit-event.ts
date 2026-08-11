import type { AuditEvent } from '../../../entities/audit-event';
import { fixtureActors } from './actors';
import { FIXTURE_BASE_TIME, fixtureScope } from './scope';

export const fixtureAuditEvent: AuditEvent = Object.freeze({
  eventId: 'evt_fx_01',
  eventName: 'upload.session.created',
  actor: {
    actorId: fixtureActors.admin.actorId,
    displayName: fixtureActors.admin.displayName,
    actorType: 'USER',
  },
  scope: fixtureScope,
  resource: { type: 'upload_session', id: 'upl_fx_01' },
  outcome: { status: 'SUCCEEDED' },
  requestId: 'req_fx_01',
  occurredAt: FIXTURE_BASE_TIME,
  catalogVersion: 'catalog_fx_v1',
  schemaVersion: 'audit_event_fx_v1',
  policyVersion: 'policy_fx_v1',
  retainUntil: '2027-08-05T08:00:00Z',
} satisfies AuditEvent);
