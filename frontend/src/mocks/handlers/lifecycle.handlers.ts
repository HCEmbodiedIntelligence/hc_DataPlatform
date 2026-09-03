import { http, HttpResponse } from 'msw';
import type { LifecyclePolicy, LifecyclePolicyCommand } from '../../features/lifecycle/api';
import { lifecycleAuditFixture, lifecyclePoliciesFixture } from '../fixtures/management';
import { getManagementScenario } from '../scenarios/management';

const pageInfo = { has_next_page: false, has_previous_page: false, start_cursor: null, end_cursor: null } as const;
let policies: LifecyclePolicy[] = lifecyclePoliciesFixture.map((policy) => ({ ...policy }));

const forbidden = () => HttpResponse.json({
  error: {
    code: 'CAPABILITY_MISSING', message: 'Capability is required.', field_errors: [],
    operation_errors: [], blocked_reasons: [], request_id: 'req_fx_management_403', retryable: false,
  },
}, { status: 403 });

function policyId(params: Readonly<Record<string, string | readonly string[] | undefined>>): string {
  return typeof params.policyId === 'string' ? params.policyId : '';
}

function updateState(id: string, state: LifecyclePolicy['state']): LifecyclePolicy | null {
  const current = policies.find((policy) => policy.policy_id === id);
  if (!current) return null;
  const updated = { ...current, state, version: current.version + 1, etag: `"v${current.version + 1}"`, updated_at: new Date().toISOString() };
  policies = policies.map((policy) => policy.policy_id === id ? updated : policy);
  return updated;
}

export default [
  http.get('*/api/v1/projects/:projectId/storage/lifecycle-policies', ({ params }) => {
    if (getManagementScenario() === 'forbidden') return forbidden();
    const projectId = typeof params.projectId === 'string' ? params.projectId : 'prj_fx_01';
    return HttpResponse.json({ project_id: projectId, items: policies.map((policy) => ({ ...policy, project_id: projectId })), page_info: pageInfo });
  }),
  http.get('*/api/v1/projects/:projectId/storage/lifecycle-audit', ({ params }) => {
    if (getManagementScenario() === 'forbidden') return forbidden();
    const projectId = typeof params.projectId === 'string' ? params.projectId : 'prj_fx_01';
    return HttpResponse.json({ project_id: projectId, items: lifecycleAuditFixture.map((event) => ({ ...event, project_id: projectId })), page_info: pageInfo });
  }),
  http.post('*/api/v1/projects/:projectId/storage/lifecycle-policies', async ({ request, params }) => {
    const projectId = typeof params.projectId === 'string' ? params.projectId : 'prj_fx_01';
    const command = await request.json() as LifecyclePolicyCommand;
    const now = new Date().toISOString();
    const created: LifecyclePolicy = { ...command, policy_id: crypto.randomUUID(), project_id: projectId, state: 'DRAFT', version: 1, etag: '"v1"', created_at: now, updated_at: now };
    policies = [...policies, created];
    return HttpResponse.json(created, { status: 201, headers: { ETag: created.etag, 'Idempotency-Replayed': 'false' } });
  }),
  http.put('*/api/v1/projects/:projectId/storage/lifecycle-policies/:policyId', async ({ request, params }) => {
    const id = policyId(params);
    const current = policies.find((policy) => policy.policy_id === id);
    if (!current) return HttpResponse.json({ error: { message: 'not found' } }, { status: 404 });
    const command = await request.json() as LifecyclePolicyCommand;
    const updated: LifecyclePolicy = { ...current, ...command, version: current.version + 1, etag: `"v${current.version + 1}"`, updated_at: new Date().toISOString() };
    policies = policies.map((policy) => policy.policy_id === id ? updated : policy);
    return HttpResponse.json(updated);
  }),
  http.post('*/api/v1/projects/:projectId/storage/lifecycle-policies/:policyId/enable', ({ params }) => {
    const updated = updateState(policyId(params), 'ENABLED');
    return updated ? HttpResponse.json(updated) : HttpResponse.json({ error: { message: 'not found' } }, { status: 404 });
  }),
  http.post('*/api/v1/projects/:projectId/storage/lifecycle-policies/:policyId/pause', ({ params }) => {
    const updated = updateState(policyId(params), 'PAUSED');
    return updated ? HttpResponse.json(updated) : HttpResponse.json({ error: { message: 'not found' } }, { status: 404 });
  }),
  http.delete('*/api/v1/projects/:projectId/storage/lifecycle-policies/:policyId', ({ params }) => {
    const id = policyId(params);
    policies = policies.filter((policy) => policy.policy_id !== id);
    return new HttpResponse(null, { status: 204, headers: { 'Idempotency-Replayed': 'false' } });
  }),
];
