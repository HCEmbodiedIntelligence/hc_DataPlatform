import { describe, expect, it } from 'vitest';
import { configureRuntime } from '../../src/shared/config/runtime';
import { configureTelemetrySink, track, type TelemetryRecord } from '../../src/shared/telemetry/track';
import { useShellStore } from '../../src/shared/scope/shell-store';

describe('telemetry redaction', () => {
  it('emits required dimensions without secrets, signed URLs, buckets, OSS keys or manifests', () => {
    configureRuntime({ apiBaseUrl: '/api/v1', sseBaseUrl: '/events', buildVersion: 'web-fixture', releaseEnv: 'test' });
    useShellStore.getState().setScope({ organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' });
    let captured: TelemetryRecord | undefined;
    configureTelemetrySink((record) => { captured = record; });
    track({
      pageId: 'P03',
      routePattern: '/ingest/uploads',
      operation: 'listUploadSessions',
      duration: 12,
      outcome: 'success',
      httpStatus: 200,
      errorCode: null,
      requestId: 'req_fx_01',
      contractVersion: 'v1',
      metadata: {
        token: 'secret-token',
        signedUrl: 'https://fixture.invalid/object?X-Amz-Signature=secret',
        bucket: 'real-bucket',
        ossKey: 'org/project/full/object/key',
        manifest: '{"raw":"manifest"}',
        opaqueObjectPath: 'org/project/full/object/key',
        objectStorageUrl: 'oss://fixture-bucket/private/object',
        safeCount: '12',
      },
    });
    expect(captured).toMatchObject({ pageId: 'P03', buildVersion: 'web-fixture', httpStatus: 200 });
    expect(captured?.scopeHash).toMatch(/^scope_[0-9a-f]{8}$/u);
    expect(captured?.metadata?.safeCount).toBe('12');
    const serialized = JSON.stringify(captured);
    expect(serialized).not.toContain('secret-token');
    expect(serialized).not.toContain('X-Amz-Signature');
    expect(serialized).not.toContain('real-bucket');
    expect(serialized).not.toContain('org/project/full/object/key');
    expect(serialized).not.toContain('fixture-bucket');
    expect(serialized).not.toContain('raw');
  });
});
