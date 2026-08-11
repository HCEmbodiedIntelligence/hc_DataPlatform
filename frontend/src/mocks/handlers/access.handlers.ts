import { http, HttpResponse } from 'msw';
import { accessBootstrapFixture, membersPageFixture } from '../fixtures/management';
import { getManagementScenario } from '../scenarios/management';

const response = (fixture: typeof accessBootstrapFixture | typeof membersPageFixture) => getManagementScenario() === 'forbidden'
  ? HttpResponse.json({
    error: {
      code: 'CAPABILITY_MISSING', message: 'Capability is required.', field_errors: [],
      operation_errors: [], blocked_reasons: [], request_id: 'req_fx_access_403', retryable: false,
    },
  }, { status: 403 })
  : HttpResponse.json(fixture);

export default [
  http.get('*/api/v1/projects/:projectId/access/bootstrap', () => response(accessBootstrapFixture)),
  http.get('*/api/v1/projects/:projectId/members', () => response(membersPageFixture)),
];
