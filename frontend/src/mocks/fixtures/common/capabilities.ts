import {
  CANONICAL_CAPABILITIES,
  DATA_PROCESSOR_CAPABILITIES,
  DEVELOPER_CAPABILITIES,
  type AuthorizationSnapshot,
} from '../../../entities/capability';
import { FIXTURE_BASE_TIME, fixtureScopeKey } from './scope';

export const fixtureCapabilitySnapshots = Object.freeze({
  PROJECT_ADMIN: {
    scopeKey: fixtureScopeKey,
    roleVersion: 'role_version_fx_admin_01',
    capabilities: CANONICAL_CAPABILITIES,
    fetchedAt: FIXTURE_BASE_TIME,
  },
  PROJECT_DEVELOPER: {
    scopeKey: fixtureScopeKey,
    roleVersion: 'role_version_fx_developer_01',
    capabilities: DEVELOPER_CAPABILITIES,
    fetchedAt: FIXTURE_BASE_TIME,
  },
  PROJECT_DATA_PROCESSOR: {
    scopeKey: fixtureScopeKey,
    roleVersion: 'role_version_fx_processor_01',
    capabilities: DATA_PROCESSOR_CAPABILITIES,
    fetchedAt: FIXTURE_BASE_TIME,
  },
} satisfies Record<string, AuthorizationSnapshot>);
