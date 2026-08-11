import { describe, expect, it } from 'vitest';
import { routes } from '../../src/features/ingest/routing';
import uploadJobRouteRecords from '../../src/pages/p03-upload-jobs/routes';
import uploadDetailRouteRecords from '../../src/pages/p04-upload-detail/routes';
import { dataSourcesQueryCodec, updateDataSourcesSearch } from '../../src/pages/p02-data-sources/query-codec';
import { uploadJobsQueryCodec, updateUploadJobsSearch } from '../../src/pages/p03-upload-jobs/query-codec';
import { uploadDetailQueryCodec, updateUploadDetailSearch } from '../../src/pages/p04-upload-detail/query-codec';
import {
  dataSourcePageWireSchema,
  dataSourceWireSchema,
  uploadCreationOptionsEnvelopeWireSchema,
  uploadEventPageWireSchema,
  uploadObjectPageWireSchema,
  uploadSessionBootstrapWireSchema,
  uploadSessionPageWireSchema,
  verificationRunPageWireSchema,
} from '../../src/features/ingest/api/wire-schemas';
import {
  dataSourceFixture,
  dataSourcePageFixture,
  uploadCreationOptionsFixture,
  uploadEventPageFixture,
  uploadBootstrapFixture,
  uploadListFixture,
  uploadObjectPageFixture,
  verificationRunPageFixture,
} from '../../src/mocks/fixtures/ingest';
import { adaptDataSource, adaptUploadBootstrap } from '../../src/features/ingest/api/adapters';
import { assertNoCredentialLeak } from '../../src/features/ingest/connectors/credential-boundary';
import { createIdleMutation, transitionMutation } from '../../src/features/ingest/mutation-machine';
import {
  canTransitionQuarantine,
  parseValidationStageCode,
  validatePipeline,
} from '../../src/features/ingest/validation-pipeline';
import { mergeUploadProgressEvent } from '../../src/features/ingest/upload/progress-events';
import { UploadAuthorizationVault, UploadResourceError, withSingleAuthorizationRefresh } from '../../src/features/ingest/upload/authorization-vault';
import { AuthorizationRefreshCoordinator } from '../../src/features/ingest/upload/authorization-coordinator';
import { effectiveUploadConcurrency, uploadRetryDelayMs } from '../../src/features/ingest/upload/policy-scheduler';
import { serializeRecoveryDescriptor } from '../../src/features/ingest/upload/recovery-descriptor';
import { MultipartController, type MultipartOssPort, type UploadedPartFact } from '../../src/features/ingest/upload/multipart-controller';
import { MultipartUploadRunner } from '../../src/features/ingest/upload/multipart-runner';

describe('ingest route contract', () => {
  it('exports the frozen upload detail builder and rejects mutable aliases', () => {
    expect(routes.uploads.build({ uploadId: 'upload_fx_01' })).toBe('/ingest/uploads/upload_fx_01');
    expect(() => routes.uploads.build({ uploadId: 'latest' })).toThrow(/stable immutable ID/u);
    expect(() => routes.uploads.build({ uploadId: 'current' })).toThrow(/stable immutable ID/u);
  });

  it('never serializes secret-shaped route query keys', () => {
    expect(routes.uploadJobs.build({ q: 'ok', signedUrl: 'https://fixture.invalid/secret', securityToken: 'x' })).toBe('/ingest/uploads?q=ok');
  });

  it('keeps P04 hidden while inheriting the P03 navigation owner', () => {
    expect(uploadJobRouteRecords[0]).toMatchObject({ path: '/ingest/uploads', navigationOwnerPageId: 'P03', defaultGroupLanding: true });
    expect(uploadDetailRouteRecords[0]).toMatchObject({ path: '/ingest/uploads/:uploadId', navigationOwnerPageId: 'P03', hiddenFromNavigation: true });
  });
});

describe('ingest query codecs', () => {
  it('round-trips repeated P02 filters and clears cursors when filters change', () => {
    const value = dataSourcesQueryCodec.parse('sourceType=ROBOT&sourceType=OSS_IMPORT&after=cursor&limit=50');
    expect(value.sourceType).toEqual(['OSS_IMPORT', 'ROBOT']);
    expect(dataSourcesQueryCodec.parse(dataSourcesQueryCodec.build(value))).toEqual(value);
    const changed = updateDataSourcesSearch(value, { q: 'new' });
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();
  });

  it('rejects P03 legacy aliases and clears cursor for tab/limit/scope changes', () => {
    const value = uploadJobsQueryCodec.parse('tab=active&action=upload&after=cursor&limit=25');
    expect(value).toMatchObject({ tab: 'all', limit: 20 });
    expect(updateUploadJobsSearch({ ...value, after: 'cursor' }, { tab: 'failed' }).after).toBeUndefined();
    expect(updateUploadJobsSearch({ ...value, after: 'cursor' }, {}, true).after).toBeUndefined();
  });

  it('normalizes P04 ownership and clears tab cursor', () => {
    const value = uploadDetailQueryCodec.parse('tab=parts&objectId=object_fx&after=cursor&limit=20');
    expect(value).toMatchObject({ tab: 'parts', objectId: 'object_fx', limit: 50 });
    const changed = updateUploadDetailSearch(value, { tab: 'verification' });
    expect(changed.after).toBeUndefined();
    expect(changed.objectId).toBeUndefined();
    expect(uploadDetailQueryCodec.parse('after=next&before=previous').after).toBeUndefined();
    expect(uploadDetailQueryCodec.parse('after=next&before=previous').before).toBeUndefined();
  });
});

describe('ingest machine-readable backend contract', () => {
  it('validates P02/P03/P04 fixtures with production wire schemas', () => {
    expect(dataSourceWireSchema.safeParse(dataSourceFixture).success).toBe(true);
    expect(dataSourcePageWireSchema.safeParse(dataSourcePageFixture).success).toBe(true);
    expect(uploadSessionPageWireSchema.safeParse(uploadListFixture).success).toBe(true);
    expect(uploadSessionBootstrapWireSchema.safeParse(uploadBootstrapFixture).success).toBe(true);
    expect(uploadObjectPageWireSchema.safeParse(uploadObjectPageFixture).success).toBe(true);
    expect(verificationRunPageWireSchema.safeParse(verificationRunPageFixture).success).toBe(true);
    expect(uploadCreationOptionsEnvelopeWireSchema.safeParse(uploadCreationOptionsFixture).success).toBe(true);
    expect(uploadEventPageWireSchema.safeParse(uploadEventPageFixture).success).toBe(true);
  });

  it('adapts snake_case to camelCase and keeps ETag separate from SHA-256', () => {
    const source = adaptDataSource(dataSourceWireSchema.parse(dataSourceFixture));
    const bootstrap = adaptUploadBootstrap(uploadSessionBootstrapWireSchema.parse(uploadBootstrapFixture));
    expect(source.configVersion).toBe('7');
    expect(bootstrap.objects[0]?.multipartEtag).toBe('multipart-etag-fx-1');
    expect(bootstrap.objects[0]?.verifiedSha256).toBe('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff');
    expect(bootstrap.objects[0]?.multipartEtag).not.toBe(bootstrap.objects[0]?.verifiedSha256);
  });

  it('fails closed on unknown connectors and validation stages', () => {
    const future = dataSourceWireSchema.parse({
      ...dataSourceFixture,
      source_type: 'FUTURE_CONNECTOR',
      binding: { kind: 'UNKNOWN', raw: 'FUTURE_CONNECTOR', display_name: '未来连接器' },
      configuration: { kind: 'UNKNOWN', raw_source_type: 'FUTURE_CONNECTOR', safe_projection: { display_name: '未来连接器', connector_family: null, migration_hint: null } },
    });
    expect(adaptDataSource(future).sourceType).toEqual({ kind: 'UNKNOWN', raw: 'FUTURE_CONNECTOR' });
    expect(parseValidationStageCode('FUTURE_STAGE')).toEqual({ kind: 'UNKNOWN', raw: 'FUTURE_STAGE' });
  });
});

describe('mutation, SSE and secret boundaries', () => {
  it('enforces the standard mutation lifecycle', () => {
    const validating = transitionMutation(createIdleMutation(), 'validating', { intentKey: 'idem-1' });
    const confirming = transitionMutation(validating, 'confirming');
    expect(transitionMutation(confirming, 'submitting').phase).toBe('submitting');
    expect(() => transitionMutation(validating, 'succeeded')).toThrow(/Invalid mutation transition/u);
  });

  it('drops duplicate/out-of-order upload events', () => {
    const current = adaptUploadBootstrap(uploadSessionBootstrapWireSchema.parse(uploadBootstrapFixture)).session;
    const older = { ...current, resourceVersion: '7' as typeof current.resourceVersion };
    const seen = new Set<string>();
    expect(mergeUploadProgressEvent(current, { eventId: 'evt-1', scopeKey: 'scope', resourceId: current.uploadId, resourceVersion: '7', safeSnapshot: older }, 'scope', seen)).toBe(current);
    expect(mergeUploadProgressEvent(current, { eventId: 'evt-1', scopeKey: 'scope', resourceId: current.uploadId, resourceVersion: '7', safeSnapshot: older }, 'scope', seen)).toBe(current);
  });

  it('rejects ordinary secret fields and keeps vault non-serializable', () => {
    expect(() => assertNoCredentialLeak({ nested: { security_token: 'fixture-only' } })).toThrow(/forbidden credential field/u);
    const vault = new UploadAuthorizationVault();
    vault.replace('upload_fx', { authorizationId: 'auth', issuedAt: '2026-08-05T08:00:00Z', expiresAt: '2026-08-05T08:15:00Z', refreshAfter: '2026-08-05T08:10:00Z', ossRegion: 'cn-hangzhou', endpoint: 'https://fixture.invalid', bucket: 'fixture', objectPrefix: 'fixture/', credentials: { accessKeyId: 'fixture', accessKeySecret: 'fixture', securityToken: 'fixture' } });
    expect(JSON.stringify(vault)).toBe('{}');
    vault.destroy();
    expect(vault.has('upload_fx')).toBe(false);
  });

  it('refreshes an expired signed authorization only once', async () => {
    let operations = 0;
    let refreshes = 0;
    await expect(withSingleAuthorizationRefresh({ operation: () => { operations += 1; return Promise.reject(new Error('EXPIRED')); }, refresh: () => { refreshes += 1; return Promise.resolve(); }, isExpired: () => true })).rejects.toBeInstanceOf(UploadResourceError);
    expect({ operations, refreshes }).toEqual({ operations: 2, refreshes: 1 });
  });

  it('coalesces concurrent authorization renewals and obeys server upload policy', async () => {
    const coordinator = new AuthorizationRefreshCoordinator();
    let renewals = 0;
    const renew = async () => { renewals += 1; await Promise.resolve(); };
    await Promise.all([coordinator.refresh('upload_fx' as never, renew), coordinator.refresh('upload_fx' as never, renew)]);
    expect(renewals).toBe(1);
    const policy = { concurrency: { minimum: 1, preferred: 4, maximum: 6 }, retry: { maxAttemptsPerPart: 5, baseDelayMs: 100, maximumDelayMs: 1_000, jitterRatio: 0.2, retryableHttpStatuses: [429, 500] } };
    expect(effectiveUploadConcurrency(policy, 2)).toBe(2);
    expect(uploadRetryDelayMs(policy, 2, 0.5)).toBe(200);
  });

  it('reconciles server parts, uploads only missing parts and computes content SHA-256 separately', async () => {
    const uploaded: UploadedPartFact[] = [];
    const port: MultipartOssPort = {
      listParts() { return Promise.resolve([]); },
      uploadPart(input) {
        const fact = { partNumber: input.partNumber, sizeBytes: String(input.bytes.size), etag: `etag-${input.partNumber}`, checksumAlgorithm: 'CRC64_ECMA', checksumValue: `crc-${input.partNumber}`, attempt: input.attempt };
        uploaded.push(fact);
        return Promise.resolve(fact);
      },
      completeMultipart(_uploadId, _objectId, parts) { expect(parts).toHaveLength(2); return Promise.resolve(); },
      abortInFlight() { return Promise.resolve(); },
      destroySecrets() {},
    };
    const controller = new MultipartController(port, () => Promise.resolve(), () => false);
    const runner = new MultipartUploadRunner(controller, { digest: () => Promise.resolve('a'.repeat(64)) }, 2);
    const result = await runner.uploadObject({
      uploadId: 'upload_fx' as never,
      plan: { uploadObjectId: 'object_fx', clientObjectId: 'local_1', relativePath: 'episode/data.bin', sizeBytes: '6', multipartUploadId: 'multipart_fx', objectKey: 'private-key', partSizeBytes: '3', confirmedParts: [] },
      file: new File([new Uint8Array(6)], 'data.bin'),
      policy: { concurrency: { minimum: 1, preferred: 2, maximum: 3 }, retry: { maxAttemptsPerPart: 2, baseDelayMs: 1, maximumDelayMs: 2, jitterRatio: 0, retryableHttpStatuses: [500] } },
      signal: new AbortController().signal,
    });
    expect(uploaded.map((part) => part.partNumber).sort()).toEqual(['1', '2']);
    expect(result.contentSha256).toBe('a'.repeat(64));
    expect(result.parts[0]?.etag).not.toBe(result.contentSha256);
  });

  it('allows only non-secret recovery descriptor fields', () => {
    const serialized = serializeRecoveryDescriptor({ schemaVersion: 1, uploadId: 'upload_fx', uploadObjectId: 'object_fx', clientObjectId: 'local_1', fileFingerprint: { sizeBytes: '42', lastModifiedAt: '2026-08-05T08:00:00Z', sampleSha256: null }, confirmedPartNumbers: ['1'], descriptorVersion: '1', updatedAt: '2026-08-05T08:01:00Z' });
    expect(serialized).not.toMatch(/token|bucket|endpoint|object_key|multipart/iu);
  });

  it('keeps quarantine transitions append-only and validates skipped stages', () => {
    expect(canTransitionQuarantine('OPEN', 'REVERIFY_REQUESTED')).toBe(true);
    expect(canTransitionQuarantine('REPLACED', 'OPEN')).toBe(false);
    expect(validatePipeline([{ code: 'MANIFEST_SCHEMA', status: 'SKIPPED', startedAt: null, finishedAt: null, jobId: null, findingCount: '0', retryable: false, skipReason: null }])).toContain('SKIPPED_STAGE_REQUIRES_REASON');
  });
});
