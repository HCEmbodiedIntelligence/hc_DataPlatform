import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DATA_PROCESSOR_CAPABILITIES, DEVELOPER_CAPABILITIES } from '../../src/entities/capability';
import { adaptAnnotationTaskDetail } from '../../src/features/annotation/api/adapter';
import { annotationTaskDetailEnvelopeWireSchema } from '../../src/features/annotation/api/wire-schemas';
import { configureAnnotationTransport } from '../../src/features/annotation/api/transport';
import type { AnnotationRequest } from '../../src/features/annotation/api/transport';
import { materializeAnnotationTaskEntry, resolveAnnotationTaskEntry, submitAnnotationTask } from '../../src/features/annotation/api/client';
import { decideAnnotationCapability } from '../../src/features/annotation/capabilities';
import { annotationJsonPointerToFieldName, jsonPointerToFieldName, SchemaDrivenAnnotationForm } from '../../src/features/annotation/forms/SchemaDrivenAnnotationForm';
import { LOCAL_ANNOTATION_DRAFT_TTL_MS, loadLocalAnnotationDraft, saveLocalAnnotationDraft } from '../../src/features/annotation/drafts/local-draft';
import { acceptedAnnotationJobFixture, annotationFixtureScope, annotationFormDefinitionWire, annotationTaskFixtures, makeAnnotationDetailEnvelope, makeAnnotationDraft, makeAnnotationTask } from '../../src/mocks/fixtures/annotation';
import { annotationQueueQueryCodec, annotationTaskQueryCodec } from '../../src/pages/p08-data-annotation/query-codec';
import { annotationRoutes, p08RouteRecords } from '../../src/pages/p08-data-annotation/routes';

describe('P08 annotation contract', () => {
  it('validates fixture through wire Zod and the production adapter', () => {
    const wire = annotationTaskDetailEnvelopeWireSchema.parse(makeAnnotationDetailEnvelope());
    const detail = adaptAnnotationTaskDetail(wire);
    expect(detail.task.id).toBe('ann-task-progress-01');
    expect(detail.streams).toHaveLength(2);
    expect(detail.formDefinition?.fields).toHaveLength(annotationFormDefinitionWire.fields.length);
    const stale = adaptAnnotationTaskDetail(annotationTaskDetailEnvelopeWireSchema.parse(makeAnnotationDetailEnvelope(annotationTaskFixtures.stale)));
    expect(stale.staleInfo?.replacementRevisionId).toBe('revision_fx_02');
    expect(stale.draft.state).toBe('STALE_READ_ONLY');
  });

  it('roundtrips queue/task codecs and strips fixed-resource injection', () => {
    const queue = annotationQueueQueryCodec.parse('queue=claimable&state=STALE,IN_PROGRESS&limit=100&after=cursor&q=%20arm%20');
    expect(annotationQueueQueryCodec.parse(annotationQueueQueryCodec.build(queue))).toEqual(queue);
    const task = annotationTaskQueryCodec.parse('t=1.250&panel=issues&zoomStartNs=2&zoomEndNs=9&revisionId=evil&schemaVersionId=evil&intent=submit&returnTo=%2Fannotations');
    expect(annotationTaskQueryCodec.parse(annotationTaskQueryCodec.build(task))).toEqual(task);
    expect(annotationTaskQueryCodec.build(task)).not.toMatch(/revisionId|schemaVersionId|intent/);
    expect(annotationRoutes.task.build({ taskId: 'task/a' })).toBe('/annotations/tasks/task%2Fa');
    expect(p08RouteRecords[1]?.navigationOwnerPageId).toBe('P08');
    expect(p08RouteRecords[1]?.hiddenFromNavigation).toBe(true);
  });

  it('fails the three unsigned owner capabilities closed and never uses role names as gates', () => {
    const capabilities = new Set<string>(['annotation_task.rebase', 'annotation_draft.edit', 'annotation_set.read']);
    expect(decideAnnotationCapability(capabilities, 'annotation_task.rebase').state).toBe('feature-unavailable');
    expect(decideAnnotationCapability(capabilities, 'annotation_draft.edit').state).toBe('feature-unavailable');
    expect(decideAnnotationCapability(capabilities, 'annotation_set.read').state).toBe('feature-unavailable');
    expect(DATA_PROCESSOR_CAPABILITIES).not.toContain('annotation_task.create');
    expect(DATA_PROCESSOR_CAPABILITIES).not.toContain('annotation_task.assign');
    expect(DATA_PROCESSOR_CAPABILITIES).not.toContain('annotation.review');
    expect(DEVELOPER_CAPABILITIES).toContain('annotation.review');
  });

  it('maps stable JSON Pointers and routes unknown field errors to the form summary', () => {
    expect(jsonPointerToFieldName('/entries/0/label_code')).toBe('entries.0.label_code');
    expect(annotationJsonPointerToFieldName('/entries/0/label_code')).toBe('labelCode');
    expect(annotationJsonPointerToFieldName('/entries/0/attributes/confidence')).toBe('confidence');
    expect(jsonPointerToFieldName('/__proto__/polluted')).toBeNull();
  });

  it('renders unknown schema field types read-only and disables mutation', () => {
    render(<SchemaDrivenAnnotationForm definition={{ schemaVersionId: 'schema:1', title: 'Fixture Schema', fields: [{ key: 'future', label: 'Future', type: 'future-vector' }] }} defaultValues={{ future: [1, 2] }} onSubmit={() => undefined} />);
    expect(screen.getByText(/未知字段类型/)).toBeVisible();
    expect(screen.getByRole('button', { name: '应用到工作副本' })).toBeDisabled();
  });

  it('keeps only allowlisted non-sensitive local fields and expires them at the explicit TTL', () => {
    sessionStorage.clear();
    const identity = { projectId: 'prj', regionCode: 'region', taskId: 'task', taskEtag: 'etag', baselineRevision: 1 };
    const definition = { schemaVersionId: 'schema', title: 'schema', fields: [{ key: 'label', label: 'Label', type: 'string', localCache: true }, { key: 'secret', label: 'Secret', type: 'string' }] } as const;
    saveLocalAnnotationDraft(identity, definition, { label: 'safe-code', secret: 'must-not-persist' }, 1000);
    expect(loadLocalAnnotationDraft(identity, 1000)).toEqual({ label: 'safe-code' });
    expect(loadLocalAnnotationDraft(identity, 1000 + LOCAL_ANNOTATION_DRAFT_TTL_MS + 1)).toBeNull();
  });

  it('treats HTTP 202 job payloads as pending instead of optimistic completion', async () => {
    const restore = configureAnnotationTransport({ request: () => Promise.resolve(acceptedAnnotationJobFixture) });
    await expect(submitAnnotationTask({ projectId: 'prj', regionCode: 'region', taskId: 'task', etag: '"etag"', idempotencyKey: 'idem' }, { expectedRevision: 1, expectedContentHash: `sha256:${'0'.repeat(64)}`, preflightId: null }))
      .resolves.toMatchObject({ kind: 'accepted-job', jobId: 'job_fx_annotation_submit', status: 'QUEUED' });
    restore();
  });

  it('uses the Resolver decision and navigates only with the server-returned taskId', async () => {
    const requests: AnnotationRequest[] = [];
    const createdTask = makeAnnotationTask({
      task_id: 'ann-task-server-returned-01',
      workflow_status: 'ASSIGNED',
      current_draft_revision: 0,
      current_draft_hash: `sha256:${'1'.repeat(64)}`,
      etag: '"task-rv-1"',
    });
    const restore = configureAnnotationTransport({
      request: (request) => {
        requests.push(request);
        if (request.method === 'GET') return Promise.resolve({
          data: {
            resolution_id: 'annotation_resolution_fx_01',
            state: 'CAN_CREATE',
            resolved_context: {
              source: annotationTaskFixtures.progress.source,
              ontology: annotationTaskFixtures.progress.ontology,
              coverage_key_hash: `sha256:${'2'.repeat(64)}`,
            },
            task_id: null,
            task_etag: null,
            entry_resolution_token: 'fixture-entry-resolution-token-00001',
            next_action: 'CREATE_TASK',
            blocked_reason: null,
            expires_at: '2026-08-05T08:15:00Z',
          },
          scope: annotationFixtureScope,
          request_id: 'req_annotation_resolution_fx_01',
          contract_version: 'data-annotation.v1',
        });
        return Promise.resolve({
          data: { disposition: 'CREATED', task: createdTask, draft: makeAnnotationDraft(createdTask.task_id, 0) },
          scope: annotationFixtureScope,
          request_id: 'req_annotation_materialize_fx_01',
          contract_version: 'data-annotation.v1',
        });
      },
    });
    const resolution = await resolveAnnotationTaskEntry({
      projectId: annotationFixtureScope.project_id,
      regionCode: annotationFixtureScope.region_code,
      revisionId: annotationTaskFixtures.progress.source.base_revision_id,
      startNs: annotationTaskFixtures.progress.source.start_ns,
      endNs: annotationTaskFixtures.progress.source.end_ns,
      streamIds: annotationTaskFixtures.progress.source.stream_ids,
    });
    expect(resolution.kind).toBe('CAN_CREATE');
    if (resolution.kind !== 'CAN_CREATE') throw new Error('Expected CAN_CREATE fixture');
    const result = await materializeAnnotationTaskEntry(
      { projectId: annotationFixtureScope.project_id, regionCode: annotationFixtureScope.region_code },
      resolution.schemaOptions[0]!,
      'client_session_fx_01',
      'idempotency_fx_01',
    );
    expect(result.taskId).toBe('ann-task-server-returned-01');
    expect(requests[1]?.body).toEqual({ entry_resolution_token: 'fixture-entry-resolution-token-00001', client_session_id: 'client_session_fx_01' });
    expect(requests[1]?.body).not.toHaveProperty('source');
    restore();
  });
});
