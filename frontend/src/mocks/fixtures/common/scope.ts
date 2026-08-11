import { makeScopeKey, type Scope } from '../../../entities/scope';

export const FIXTURE_BASE_TIME = '2026-08-05T08:00:00Z';

export const fixtureScope: Scope = Object.freeze({
  organizationId: 'org_fx_01',
  projectId: 'prj_fx_01',
  regionCode: 'cn-shanghai',
});

export const fixtureScopeKey = makeScopeKey(fixtureScope);
