import { delay, http, HttpResponse } from 'msw';
import { getDatasetScenario, resetDatasetScenario } from '../scenarios/datasets';
import {
  approveResultFixture,
  asyncJobAcceptedFixture,
  datasetBootstrapFixture,
  datasetFacetsFixture,
  datasetFixtureScope,
  datasetIds,
  datasetListFixture,
  datasetSummaryFixture,
  datasetVersionCapacityFixture,
  datasetVersionSchemaFixture,
  datasetWire,
  datasetsPageCapabilitiesFixture,
  deletionPreflightFixture,
  episodePageFixture,
  episodeRevisionFixture,
  manifestFixture,
  operationalInventoryFixture,
  requiredStorageFixture,
  responseMeta,
  returnResultFixture,
  returnedVersionWire,
  reviewChecksFixture,
  reviewingVersionWire,
  sharedJobFixture,
  sourceProvenanceFixture,
  versionBootstrapFixture,
  versionPageFixture,
  versionSchemaFixture,
} from '../fixtures/datasets/core';

const api = '*/projects/:projectId';
const returnReviewPath =
  /.*\/projects\/(?<projectId>[^/]+)\/datasets\/(?<datasetId>[^/]+)\/versions\/(?<versionId>[^/]+):return$/u;
const approveReviewPath =
  /.*\/projects\/(?<projectId>[^/]+)\/datasets\/(?<datasetId>[^/]+)\/versions\/(?<versionId>[^/]+):approve$/u;
const idempotency = new Map<string, string>();
let jobPoll = 0;
let reviewOutcome: 'APPROVED' | 'RETURNED' | null = null;
const datasetJobIds = new Set(['job_fx_manifest_materialization', 'job_fx_version_diff']);
const error = (
  status: number,
  code: string,
  message: string,
  extras: Record<string, unknown> = {},
) =>
  HttpResponse.json(
    {
      error: {
        code,
        message,
        field_errors: [],
        operation_errors: [],
        blocked_reasons: [],
        request_id: `req_fx_${code.toLowerCase()}`,
        retryable: status >= 500,
        ...extras,
      },
    },
    { status },
  );
function validate(
  request: Request,
  params: Record<string, string | readonly string[] | undefined>,
  write = false,
  etag = false,
): Response | null {
  if (params.projectId !== datasetFixtureScope.project_id)
    return error(404, 'NOT_FOUND', '资源不属于当前 Scope');
  if (!request.headers.get('X-Client-Version'))
    return error(400, 'MISSING_HEADER', '缺少 X-Client-Version');
  if (request.headers.get('X-Organization-Id') !== datasetFixtureScope.organization_id)
    return error(400, 'SCOPE_HEADER_MISMATCH', 'Organization Header 不一致');
  if (request.headers.get('X-Project-Id') !== datasetFixtureScope.project_id)
    return error(400, 'SCOPE_HEADER_MISMATCH', 'Project Header 不一致');
  if (request.headers.get('X-Region-Code') !== datasetFixtureScope.region_code)
    return error(400, 'SCOPE_HEADER_MISMATCH', 'Region Header 不一致');
  const url = new URL(request.url);
  if (url.searchParams.has('after') && url.searchParams.has('before'))
    return error(400, 'INVALID_CURSOR', 'after 与 before 互斥');
  if (write && !request.headers.get('Idempotency-Key'))
    return error(400, 'IDEMPOTENCY_KEY_REQUIRED', '缺少 Idempotency-Key');
  if (etag && !request.headers.get('If-Match'))
    return error(428, 'PRECONDITION_REQUIRED', '缺少 If-Match');
  return null;
}
function validateKnownPath(
  params: Record<string, string | readonly string[] | undefined>,
): Response | null {
  if (
    params.datasetId &&
    ![datasetIds.dataset, datasetIds.empty].includes(
      String(params.datasetId) as typeof datasetIds.dataset | typeof datasetIds.empty,
    )
  ) {
    return error(404, 'DATASET_NOT_FOUND', 'Dataset 不存在');
  }
  if (
    params.versionId &&
    ![datasetIds.reviewing, datasetIds.returned, datasetIds.ready].includes(
      String(params.versionId) as
        | typeof datasetIds.reviewing
        | typeof datasetIds.returned
        | typeof datasetIds.ready,
    )
  ) {
    return error(404, 'VERSION_NOT_FOUND', 'Version 不存在');
  }
  if (params.episodeId && params.episodeId !== datasetIds.episode)
    return error(404, 'EPISODE_NOT_FOUND', 'Episode 不存在');
  if (params.revisionId && params.revisionId !== datasetIds.revision)
    return error(404, 'REVISION_NOT_FOUND', 'Revision 不存在');
  return null;
}
function validateQuery(request: Request, allowed: readonly string[]): Response | null {
  const allowedKeys = new Set(allowed);
  for (const key of new URL(request.url).searchParams.keys()) {
    if (!allowedKeys.has(key))
      return error(400, 'UNKNOWN_QUERY_PARAMETER', `不支持的 Query: ${key}`);
  }
  return null;
}
async function recordIdempotency(request: Request): Promise<Response | null> {
  const key = request.headers.get('Idempotency-Key');
  if (!key) return error(400, 'IDEMPOTENCY_KEY_REQUIRED', '缺少 Idempotency-Key');
  const body = await request.clone().text();
  const previous = idempotency.get(key);
  if (previous !== undefined && previous !== body)
    return error(409, 'IDEMPOTENCY_CONFLICT', '同一幂等键不能提交不同 Body');
  idempotency.set(key, body);
  return null;
}
async function waitScenario() {
  if (getDatasetScenario() === 'first-loading') await delay(2_000);
  if (getDatasetScenario() === 'submitting') await delay(1_200);
}
function readFailure(detail = false): Response | null {
  const s = getDatasetScenario();
  if (s === 'forbidden') return error(403, 'FORBIDDEN', '无权限');
  if (s === 'fatal-error' && !detail) return error(500, 'SERVER_ERROR', 'Fixture 服务错误');
  if (s === 'not-found' && detail) return error(404, 'NOT_FOUND', '资源不存在');
  if (s === 'gone' && detail) return error(410, 'GONE', '资源已失效');
  if (s === 'rate-limited') return error(429, 'RATE_LIMITED', '稍后重试');
  return null;
}
async function validateReturnBody(request: Request): Promise<Response | null> {
  const body = (await request
    .clone()
    .json()
    .catch(() => null)) as Record<string, unknown> | null;
  if (
    !body ||
    body.expected_status !== 'REVIEWING' ||
    !Array.isArray(body.findings) ||
    body.findings.length < 1
  )
    return error(422, 'VALIDATION_ERROR', '至少需要一条 Finding', {
      field_errors: [{ path: '/findings', code: 'MIN_ITEMS', message: '至少一条' }],
      operation_errors: [{ code: 'REVIEW_RETURN_INVALID', message: '退回命令无效' }],
    });
  return null;
}

export const datasetHandlers = [
  http.get(`${api}/datasets\\:page-capabilities`, ({ request, params }) => {
    const invalid = validate(request, params);
    if (invalid) return invalid;
    const failed = readFailure();
    if (failed) return failed;
    return HttpResponse.json(datasetsPageCapabilitiesFixture);
  }),
  http.get(`${api}/datasets\\:summary`, ({ request, params }) => {
    const invalid = validate(request, params);
    if (invalid) return invalid;
    if (getDatasetScenario() === 'partial-error')
      return error(500, 'SERVER_ERROR', 'Summary 区域不可用');
    const empty = getDatasetScenario() === 'empty' || getDatasetScenario() === 'filtered-empty';
    return HttpResponse.json(
      empty
        ? {
            ...datasetSummaryFixture,
            data: {
              ...datasetSummaryFixture.data,
              dataset_count: '0',
              episode_count: '0',
              pending_review_version_count: '0',
              returned_version_count: '0',
              actionable_draft_count: '0',
            },
          }
        : datasetSummaryFixture,
    );
  }),
  http.get(`${api}/datasets\\:facets`, ({ request, params }) => {
    const invalid = validate(request, params);
    if (invalid) return invalid;
    if (getDatasetScenario() === 'partial-error')
      return error(500, 'SERVER_ERROR', 'Facets 区域不可用');
    return HttpResponse.json(datasetFacetsFixture);
  }),
  http.get(`${api}/datasets`, async ({ request, params }) => {
    const invalid =
      validate(request, params) ??
      validateQuery(request, [
        'q',
        'robot_model_id',
        'robot_id',
        'task',
        'scene',
        'asset_state',
        'storage_class',
        'channels',
        'channel_match',
        'created_from',
        'created_to',
        'sort',
        'after',
        'before',
        'limit',
      ]);
    if (invalid) return invalid;
    const url = new URL(request.url);
    if (
      ![
        'activity_at:desc,dataset_id:desc',
        'created_at:desc,dataset_id:desc',
        'name:asc,dataset_id:asc',
      ].includes(url.searchParams.get('sort') ?? '')
    )
      return error(400, 'INVALID_FILTER', 'sort 缺少稳定 ID tie-breaker');
    if (!['20', '50', '100'].includes(url.searchParams.get('limit') ?? ''))
      return error(400, 'INVALID_FILTER', 'limit 不在白名单');
    await waitScenario();
    const failed = readFailure();
    if (failed) return failed;
    const s = getDatasetScenario();
    if (s === 'contract-mismatch')
      return HttpResponse.json({
        ...datasetListFixture,
        leaked_signed_url: 'https://fixture.invalid/leak',
      });
    if (s === 'unknown-enum')
      return HttpResponse.json({
        ...datasetListFixture,
        items: [
          {
            ...datasetListFixture.items[0],
            current_version: {
              ...datasetListFixture.items[0].current_version,
              kind: 'FUTURE_KIND',
            },
          },
        ],
      });
    if (
      s === 'empty' ||
      s === 'filtered-empty' ||
      url.searchParams.get('q') === 'missing' ||
      (url.searchParams.has('task') &&
        url.searchParams.get('task') !== episodePageFixture.items[0]?.task)
    )
      return HttpResponse.json({ ...datasetListFixture, items: [] });
    if (url.searchParams.has('task')) {
      return HttpResponse.json({ ...datasetListFixture, items: [datasetListFixture.items[0]] });
    }
    if (s === 'cursor-pagination') {
      const secondWindow = url.searchParams.get('after') === 'cursor_fx_next';
      return HttpResponse.json({
        ...datasetListFixture,
        items: secondWindow ? [datasetListFixture.items[1]] : [datasetListFixture.items[0]],
        page_info: secondWindow
          ? { ...datasetListFixture.page_info, before: 'cursor_fx_previous', has_previous: true }
          : { ...datasetListFixture.page_info, after: 'cursor_fx_next', has_next: true },
      });
    }
    return HttpResponse.json(datasetListFixture);
  }),
  http.post(`${api}/datasets`, async ({ request, params }) => {
    const invalid = validate(request, params, true);
    if (invalid) return invalid;
    await waitScenario();
    if (getDatasetScenario() === 'validation-error')
      return error(422, 'VALIDATION_ERROR', '字段校验失败', {
        field_errors: [{ path: '/name', code: 'REQUIRED', message: '名称必填' }],
        operation_errors: [{ code: 'CREATE_INVALID', message: '无法创建' }],
      });
    const bodyText = await request.clone().text();
    const key = request.headers.get('Idempotency-Key')!;
    const previous = idempotency.get(key);
    if (previous && previous !== bodyText)
      return error(409, 'IDEMPOTENCY_MISMATCH', '同一幂等键的 Body 不同');
    idempotency.set(key, bodyText);
    return HttpResponse.json(
      { data: datasetWire, meta: responseMeta('req_fx_dataset_create') },
      { status: previous ? 200 : 201, headers: previous ? { 'Idempotency-Replayed': 'true' } : {} },
    );
  }),
  http.get(`${api}/datasets/:datasetId/bootstrap`, async ({ request, params }) => {
    const invalid = validate(request, params) ?? validateKnownPath(params);
    if (invalid) return invalid;
    await waitScenario();
    const failed = readFailure(true);
    if (failed) return failed;
    return HttpResponse.json(datasetBootstrapFixture);
  }),
  http.get(`${api}/datasets/:datasetId/versions`, ({ request, params }) => {
    const invalid =
      validate(request, params) ??
      validateKnownPath(params) ??
      validateQuery(request, [
        'q',
        'version_kind',
        'version_status',
        'sort',
        'after',
        'before',
        'limit',
      ]);
    if (invalid) return invalid;
    if (getDatasetScenario() === 'partial-error')
      return error(500, 'SERVER_ERROR', 'Versions 区域不可用');
    return HttpResponse.json(
      getDatasetScenario() === 'empty' ? { ...versionPageFixture, items: [] } : versionPageFixture,
    );
  }),
  http.get(`${api}/datasets/:datasetId/versions/:versionId/episodes`, ({ request, params }) => {
    const invalid =
      validate(request, params) ??
      validateKnownPath(params) ??
      validateQuery(request, [
        'snapshot_token',
        'q',
        'task',
        'robot_id',
        'success_state',
        'started_from',
        'started_to',
        'included',
        'review_status',
        'has_finding',
        'change_type',
        'sort',
        'after',
        'before',
        'limit',
      ]);
    if (invalid) return invalid;
    const requestedTask = new URL(request.url).searchParams.get('task');
    return HttpResponse.json(
      getDatasetScenario() === 'empty' ||
        (requestedTask !== null && requestedTask !== episodePageFixture.items[0]?.task)
        ? { ...episodePageFixture, items: [] }
        : {
            ...episodePageFixture,
            items: episodePageFixture.items.map((item) => ({
              ...item,
              version_id: String(params.versionId),
            })),
          },
    );
  }),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/schema-summary`,
    ({ request, params }) => {
      const invalid = validate(request, params);
      if (invalid) return invalid;
      if (getDatasetScenario() === 'partial-error')
        return error(500, 'SERVER_ERROR', 'Schema 区域不可用');
      return HttpResponse.json({
        ...datasetVersionSchemaFixture,
        data: {
          ...datasetVersionSchemaFixture.data,
          dataset_id: String(params.datasetId),
          version_id: String(params.versionId),
        },
      });
    },
  ),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/source-provenance`,
    ({ request, params }) => {
      const invalid = validate(request, params);
      if (invalid) return invalid;
      if (getDatasetScenario() === 'partial-error')
        return error(500, 'SERVER_ERROR', 'Sources 区域不可用');
      return HttpResponse.json({
        ...sourceProvenanceFixture,
        items: sourceProvenanceFixture.items.map((item) => ({
          ...item,
          dataset_id: String(params.datasetId),
          version_id: String(params.versionId),
        })),
      });
    },
  ),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/capacity-facts`,
    ({ request, params }) => {
      const invalid = validate(request, params);
      if (invalid) return invalid;
      if (getDatasetScenario() === 'partial-error')
        return error(500, 'SERVER_ERROR', 'Capacity 区域不可用');
      const data = {
        ...datasetVersionCapacityFixture.data,
        dataset_id: String(params.datasetId),
        version_id: String(params.versionId),
      };
      return HttpResponse.json(
        getDatasetScenario() === 'unknown-enum'
          ? { ...datasetVersionCapacityFixture, data: { ...data, state: 'FUTURE_STATE' } }
          : { ...datasetVersionCapacityFixture, data },
      );
    },
  ),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/bootstrap`,
    async ({ request, params }) => {
      const invalid = validate(request, params) ?? validateKnownPath(params);
      if (invalid) return invalid;
      await waitScenario();
      const failed = readFailure(true);
      if (failed) return failed;
      const requestedVersionId = String(params.versionId);
      let version: Record<string, unknown> = {
        ...versionBootstrapFixture.data.version,
        version_id: requestedVersionId,
      };
      if (requestedVersionId === datasetIds.returned) version = returnedVersionWire;
      if (requestedVersionId === datasetIds.reviewing && reviewOutcome === 'APPROVED') {
        version = {
          ...reviewingVersionWire,
          version_token: approveResultFixture.output_version.version_token,
          etag: '"version-review-rv-5"',
          delivery_status: 'GENERATING',
          approved_review_decision_id: approveResultFixture.review_decision.id,
          allowed_actions: [],
        };
      }
      if (requestedVersionId === datasetIds.reviewing && reviewOutcome === 'RETURNED') {
        version = {
          ...returnedVersionWire,
          version_id: datasetIds.reviewing,
          display_version: reviewingVersionWire.display_version,
          created_at: reviewingVersionWire.created_at,
          version_token: returnResultFixture.data.output_version.version_token,
          returned_from_version_id: datasetIds.reviewing,
        };
      }
      if (getDatasetScenario() === 'unknown-enum')
        version = { ...version, status: 'FUTURE_REVIEW_STATE' };
      return HttpResponse.json({
        ...versionBootstrapFixture,
        data: { ...versionBootstrapFixture.data, version_id: requestedVersionId, version },
      });
    },
  ),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/episode-revisions/:revisionId`,
    ({ request, params }) => {
      const invalid = validate(request, params);
      if (invalid) return invalid;
      if (!new URL(request.url).searchParams.get('snapshot_token'))
        return error(400, 'SNAPSHOT_REQUIRED', '缺少 snapshot_token');
      return HttpResponse.json({
        ...episodeRevisionFixture,
        data: {
          ...episodeRevisionFixture.data,
          version_id: String(params.versionId),
          revision_id: String(params.revisionId),
        },
      });
    },
  ),
  http.get(`${api}/datasets/:datasetId/versions/:versionId/manifest`, ({ request, params }) => {
    const invalid = validate(request, params);
    if (invalid) return invalid;
    if (getDatasetScenario() === 'partial-error')
      return error(500, 'SERVER_ERROR', 'Manifest 区域不可用');
    return HttpResponse.json({ ...manifestFixture, version_id: String(params.versionId) });
  }),
  http.get(`${api}/datasets/:datasetId/versions/:versionId/schema`, ({ request, params }) => {
    const invalid = validate(request, params) ?? validateKnownPath(params);
    if (invalid) return invalid;
    const snapshotToken = new URL(request.url).searchParams.get('snapshot_token');
    if (!snapshotToken) return error(400, 'SNAPSHOT_REQUIRED', '缺少 snapshot_token');
    return HttpResponse.json({
      ...versionSchemaFixture,
      data: {
        ...versionSchemaFixture.data,
        dataset_id: String(params.datasetId),
        version_id: String(params.versionId),
        snapshot_token: snapshotToken,
      },
    });
  }),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/required-storage`,
    ({ request, params }) => {
      const invalid = validate(request, params) ?? validateKnownPath(params);
      if (invalid) return invalid;
      if (!new URL(request.url).searchParams.get('snapshot_token'))
        return error(400, 'SNAPSHOT_REQUIRED', '缺少 snapshot_token');
      return HttpResponse.json({
        ...requiredStorageFixture,
        items: requiredStorageFixture.items.map((item) => ({
          ...item,
          dataset_id: String(params.datasetId),
          version_id: String(params.versionId),
        })),
      });
    },
  ),
  http.get(
    `${api}/datasets/:datasetId/versions/:versionId/operational-inventory`,
    ({ request, params }) => {
      const invalid = validate(request, params) ?? validateKnownPath(params);
      if (invalid) return invalid;
      const operationalRevision = new URL(request.url).searchParams.get('operational_revision');
      if (!operationalRevision)
        return error(400, 'OPERATIONAL_REVISION_REQUIRED', '缺少 operational_revision');
      return HttpResponse.json({
        ...operationalInventoryFixture,
        items: operationalInventoryFixture.items.map((item) => ({
          ...item,
          dataset_id: String(params.datasetId),
          version_id: String(params.versionId),
          operational_revision: operationalRevision,
        })),
      });
    },
  ),
  http.post(
    `${api}/datasets/:datasetId/versions/:versionId/review-checks`,
    ({ request, params }) => {
      const invalid = validate(request, params, false, true) ?? validateKnownPath(params);
      if (invalid) return invalid;
      if (reviewOutcome)
        return error(409, 'VERSION_STATE_CONFLICT', 'Version 已存在最终 Review 决定');
      if (getDatasetScenario() === 'permission-revoked')
        return error(403, 'FORBIDDEN', '权限已撤销');
      if (getDatasetScenario() === 'preflight-blocked')
        return HttpResponse.json({
          ...reviewChecksFixture,
          data: {
            ...reviewChecksFixture.data,
            blockers: [{ code: 'ACTIVE_JOB', message: '存在运行中的任务' }],
          },
        });
      if (getDatasetScenario() === 'token-expired')
        return HttpResponse.json({
          ...reviewChecksFixture,
          data: { ...reviewChecksFixture.data, review_token_expires_at: '2026-08-01T00:00:00Z' },
        });
      return HttpResponse.json(reviewChecksFixture);
    },
  ),
  http.post(approveReviewPath, async ({ request, params }) => {
    const invalid = validate(request, params, true, true) ?? validateKnownPath(params);
    if (invalid) return invalid;
    await waitScenario();
    const s = getDatasetScenario();
    if (s === 'etag-conflict' || s === 'conflict')
      return error(412, 'PRECONDITION_FAILED', 'ETag 已变化');
    if (s === 'permission-revoked') return error(403, 'FORBIDDEN', '预检后权限已撤销');
    if (s === 'atomic-failure') return error(500, 'SERVER_ERROR', '原子事务已整体回滚');
    const replayConflict = await recordIdempotency(request);
    if (replayConflict) return replayConflict;
    reviewOutcome = 'APPROVED';
    return HttpResponse.json(
      {
        ...approveResultFixture,
        output_version: { ...approveResultFixture.output_version, id: String(params.versionId) },
        review_decision: {
          ...approveResultFixture.review_decision,
          output_version_id: String(params.versionId),
        },
      },
      { status: 202 },
    );
  }),
  http.post(returnReviewPath, async ({ request, params }) => {
    const invalid = validate(request, params, true, true) ?? validateKnownPath(params);
    if (invalid) return invalid;
    await waitScenario();
    const bodyInvalid = await validateReturnBody(request);
    if (bodyInvalid) return bodyInvalid;
    const s = getDatasetScenario();
    if (s === 'etag-conflict' || s === 'conflict')
      return error(412, 'PRECONDITION_FAILED', 'ETag 已变化', {
        latest_resource_ref: {
          dataset_id: datasetIds.dataset,
          version_id: datasetIds.reviewing,
          etag: '"version-review-rv-5"',
        },
      });
    if (s === 'permission-revoked') return error(403, 'FORBIDDEN', '预检后权限已撤销');
    if (s === 'atomic-failure') return error(500, 'SERVER_ERROR', '原子事务已整体回滚');
    const replayConflict = await recordIdempotency(request);
    if (replayConflict) return replayConflict;
    const body = (await request.clone().json()) as { findings: Array<Record<string, string>> };
    const findings = body.findings.map((finding, index) => ({
      ...returnResultFixture.data.findings[0],
      ...finding,
      id: `review_finding_fx_${index + 1}`,
    }));
    if (s === 'return-partial-response')
      return HttpResponse.json({
        ...returnResultFixture,
        data: { ...returnResultFixture.data, successor_draft_id: undefined },
      });
    reviewOutcome = 'RETURNED';
    return HttpResponse.json({
      ...returnResultFixture,
      data: {
        ...returnResultFixture.data,
        output_version: {
          ...returnResultFixture.data.output_version,
          id: String(params.versionId),
        },
        review_decision: {
          ...returnResultFixture.data.review_decision,
          output_version_id: String(params.versionId),
        },
        findings,
        review_finding_ids: findings.map((finding) => finding.id),
        returned_from_version_id: String(params.versionId),
      },
    });
  }),
  http.post(`${api}/datasets/:datasetId/deletion-checks`, ({ request, params }) => {
    const invalid = validate(request, params, true, true) ?? validateKnownPath(params);
    if (invalid) return invalid;
    return HttpResponse.json({
      ...deletionPreflightFixture,
      resource_type: 'DATASET',
      resource_id: String(params.datasetId),
    });
  }),
  http.post(
    `${api}/datasets/:datasetId/versions/:versionId/deletion-checks`,
    ({ request, params }) => {
      const invalid = validate(request, params, true, true) ?? validateKnownPath(params);
      if (invalid) return invalid;
      return HttpResponse.json({
        ...deletionPreflightFixture,
        resource_id: String(params.versionId),
      });
    },
  ),
  http.post(`${api}/datasets/:datasetId/versions/:versionId/diff-jobs`, ({ request, params }) => {
    const invalid = validate(request, params, true, true) ?? validateKnownPath(params);
    if (invalid) return invalid;
    return HttpResponse.json(asyncJobAcceptedFixture, { status: 202 });
  }),
  http.get('*/api/v1/jobs/:jobId', ({ request, params }) => {
    const jobId = String(params.jobId);
    if (!datasetJobIds.has(jobId)) return undefined;
    if (!request.headers.get('X-Client-Version'))
      return error(400, 'MISSING_HEADER', '缺少 X-Client-Version');
    jobPoll += 1;
    const status =
      getDatasetScenario() === 'job-failed'
        ? 'FAILED'
        : jobPoll === 1
          ? 'QUEUED'
          : jobPoll === 2
            ? 'RUNNING'
            : 'SUCCEEDED';
    return HttpResponse.json({
      ...sharedJobFixture,
      job_id: jobId,
      status,
      resource_version: String(Math.min(jobPoll, 3)),
    });
  }),
];

export function resetDatasetHandlerState() {
  idempotency.clear();
  jobPoll = 0;
  reviewOutcome = null;
  resetDatasetScenario();
}
export default datasetHandlers;
