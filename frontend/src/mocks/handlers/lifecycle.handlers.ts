import { http, HttpResponse } from 'msw';
import { lifecyclePageFixture } from '../fixtures/management';
import { getManagementScenario } from '../scenarios/management';

const forbidden = () => HttpResponse.json({
  error: {
    code: 'CAPABILITY_MISSING', message: 'Capability is required.', field_errors: [],
    operation_errors: [], blocked_reasons: [], request_id: 'req_fx_management_403', retryable: false,
  },
}, { status: 403 });

export default [
  http.get('*/api/v1/projects/:projectId/storage/lifecycle-page', () => {
    if (getManagementScenario() === 'forbidden') return forbidden();
    if (getManagementScenario() === 'contract-mismatch') {
      return HttpResponse.json({
        ...lifecyclePageFixture,
        data: { ...lifecyclePageFixture.data, policy_set_version: 3 },
      });
    }
    return HttpResponse.json(lifecyclePageFixture);
  }),
];
