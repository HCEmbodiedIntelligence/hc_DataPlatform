import { http, HttpResponse } from 'msw';
import {
  calibrationSetsPageFixture,
  dataSchemasPageFixture,
  robotModelsPageFixture,
  robotsPageFixture,
} from '../fixtures/management';
import { getManagementScenario } from '../scenarios/management';

const gate = () => getManagementScenario() === 'forbidden'
  ? HttpResponse.json({
    error: {
      code: 'CAPABILITY_MISSING', message: 'Capability is required.', field_errors: [],
      operation_errors: [], blocked_reasons: [], request_id: 'req_fx_robotics_403', retryable: false,
    },
  }, { status: 403 })
  : null;

export default [
  http.get('*/api/v1/organizations/:organizationId/robot-models', () => gate() ?? HttpResponse.json(robotModelsPageFixture)),
  http.get('*/api/v1/projects/:projectId/regions/:regionCode/robots', () => gate() ?? HttpResponse.json(robotsPageFixture)),
  http.get('*/api/v1/projects/:projectId/regions/:regionCode/calibration-sets', () => gate() ?? HttpResponse.json(calibrationSetsPageFixture)),
  http.get('*/api/v1/organizations/:organizationId/stream-schemas', () => gate() ?? HttpResponse.json(dataSchemasPageFixture)),
];
