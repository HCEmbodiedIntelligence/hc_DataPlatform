import { useQueryClient } from '@tanstack/react-query';
import type { IngestScope } from '../../../entities/data-source';
import type { UploadSession } from '../upload/model';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { createMutationIntentKey } from '../mutation-machine';
import type { UploadAuthorizationVault } from '../upload/authorization-vault';
import { adaptDataSource, adaptScope, adaptUploadSession, assertResponseScope } from './adapters';
import { mutateDataSource, mutateUploadSession } from './client';
import { useStandardMutation } from '../use-standard-mutation';
import { useEphemeralMutation } from '../use-ephemeral-mutation';

function resourcePrefix(resource: string): readonly unknown[] {
  return makeQueryKey('ingest', resource, {}).slice(0, 3);
}

export interface DataSourceMutationInput {
  readonly scope: IngestScope;
  readonly sourceId?: string;
  readonly body: unknown;
  readonly etag?: string;
  readonly idempotencyKey?: string;
}

export function useDataSourceMutation(operation: 'create' | 'update' | 'rotate-credential' | 'enable' | 'disable') {
  const queryClient = useQueryClient();
  return useEphemeralMutation({
    intentKeyOf: (input) => input.idempotencyKey ?? null,
    requiresConfirmation: operation === 'rotate-credential' || operation === 'enable' || operation === 'disable',
    mutationFn: async (input: DataSourceMutationInput) => {
      const wire = await mutateDataSource({
        scope: input.scope,
        sourceId: input.sourceId,
        operation,
        body: input.body,
        ifMatch: input.etag,
        idempotencyKey: input.idempotencyKey ?? createMutationIntentKey(),
      });
      if (!('data' in wire) || !('configuration' in wire.data)) throw new Error('CONTRACT_MISMATCH');
      assertResponseScope(adaptScope(wire.scope), input.scope);
      return adaptDataSource(wire.data, input.scope);
    },
    onSuccess: (source) => {
      queryClient.setQueryData(makeQueryKey('ingest', 'data-source', source.id), source);
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('data-source-page') });
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('upload-creation-options') });
    },
  });
}

export function useTestDataSourceConnection() {
  return useStandardMutation({
    intentKeyOf: (input) => input.idempotencyKey ?? null,
    isAccepted: () => true,
    mutationFn: async (input: DataSourceMutationInput & { readonly sourceId: string; readonly etag: string }) => {
      const wire = await mutateDataSource({
        scope: input.scope,
        sourceId: input.sourceId,
        operation: 'test-connection',
        body: input.body,
        ifMatch: input.etag,
        idempotencyKey: input.idempotencyKey ?? createMutationIntentKey(),
      });
      assertResponseScope(adaptScope(wire.scope), input.scope);
      return wire;
    },
  });
}

export function useUploadSessionMutation(
  operation: 'create' | 'pause' | 'resume',
  authorizationVault: UploadAuthorizationVault,
) {
  const queryClient = useQueryClient();
  return useEphemeralMutation({
    intentKeyOf: (input) => input.idempotencyKey ?? null,
    requiresPreflight: operation === 'create',
    mutationFn: async (input: {
      readonly scope: IngestScope;
      readonly uploadId?: string;
      readonly body: unknown;
      readonly etag?: string;
      readonly idempotencyKey?: string;
      readonly localFiles?: readonly File[];
    }) => {
      const wire = await mutateUploadSession({
        scope: input.scope,
        uploadId: input.uploadId,
        operation,
        body: input.body,
        ifMatch: input.etag,
        idempotencyKey: input.idempotencyKey ?? createMutationIntentKey(),
      });
      assertResponseScope(adaptScope(wire.scope), input.scope);
      if ('upload_session' in wire.data) {
        const session = adaptUploadSession(wire.data.upload_session, input.scope);
        const auth = wire.data.secret.authorization;
        authorizationVault.replace(session.uploadId, {
          authorizationId: auth.authorization_id,
          issuedAt: auth.issued_at,
          expiresAt: auth.expires_at,
          refreshAfter: auth.refresh_after,
          ossRegion: auth.oss_region,
          endpoint: auth.endpoint,
          bucket: auth.bucket,
          objectPrefix: auth.object_prefix,
          credentials: {
            accessKeyId: auth.credentials.access_key_id,
            accessKeySecret: auth.credentials.access_key_secret,
            securityToken: auth.credentials.security_token,
          },
          policy: wire.data.secret.policy ? {
            schemaVersion: wire.data.secret.policy.schema_version,
            policyVersion: wire.data.secret.policy.policy_version,
            effectiveUntil: wire.data.secret.policy.effective_until,
            concurrency: wire.data.secret.policy.concurrency,
            retry: {
              maxAttemptsPerPart: wire.data.secret.policy.retry.max_attempts_per_part,
              baseDelayMs: wire.data.secret.policy.retry.base_delay_ms,
              maximumDelayMs: wire.data.secret.policy.retry.maximum_delay_ms,
              jitterRatio: wire.data.secret.policy.retry.jitter_ratio,
              retryableHttpStatuses: wire.data.secret.policy.retry.retryable_http_statuses,
            },
          } : null,
          objectPlans: wire.data.secret.object_plans.map((plan) => ({
            uploadObjectId: plan.upload_object_id,
            clientObjectId: plan.client_object_id,
            relativePath: plan.relative_path,
            sizeBytes: plan.size_bytes,
            multipartUploadId: plan.multipart_upload_id,
            objectKey: plan.object_key,
            partSizeBytes: plan.part_size_bytes,
            confirmedParts: plan.confirmed_parts.map((part) => ({ partNumber: part.part_number, etag: part.etag, checksumAlgorithm: part.checksum_algorithm, checksumValue: part.checksum_value })),
          })),
        }, input.localFiles);
        return session;
      }
      return adaptUploadSession(wire.data, input.scope);
    },
    onSuccess: (session) => {
      void queryClient.invalidateQueries({ queryKey: makeQueryKey('ingest', 'upload-session', session.uploadId) });
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('upload-sessions') });
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('upload-summary') });
    },
  });
}

export function useRetryUploadVerification() {
  const queryClient = useQueryClient();
  return useStandardMutation({
    intentKeyOf: (input) => input.idempotencyKey ?? null,
    requiresConfirmation: true,
    isAccepted: () => true,
    mutationFn: async (input: {
      readonly scope: IngestScope;
      readonly uploadId: string;
      readonly etag: string;
      readonly failedVerificationRunId: string;
      readonly expectedObjectSetHash: string;
      readonly expectedManifestSha256: string;
      readonly reason: string;
      readonly idempotencyKey?: string;
    }) => {
      const wire = await mutateUploadSession({
        scope: input.scope,
        uploadId: input.uploadId,
        operation: 'retry-verification',
        body: {
          failed_verification_run_id: input.failedVerificationRunId,
          expected_object_set_hash: input.expectedObjectSetHash,
          expected_manifest_sha256: input.expectedManifestSha256,
          reason: input.reason,
        },
        ifMatch: input.etag,
        idempotencyKey: input.idempotencyKey ?? createMutationIntentKey(),
      });
      assertResponseScope(adaptScope(wire.scope), input.scope);
      if (!('verification_run' in wire.data)) throw new Error('CONTRACT_MISMATCH');
      return { verificationRun: wire.data.verification_run, jobId: wire.job.job_id };
    },
    onSuccess: (_result, input) => {
      void queryClient.invalidateQueries({ queryKey: makeQueryKey('ingest', 'upload-session', input.uploadId) });
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('verification-runs') });
    },
  });
}

export interface UploadBatchResult {
  readonly attempted: number;
  readonly succeeded: number;
  readonly failed: number;
}

/** Backend has no unsafe pseudo-batch endpoint: each resource gets its own ETag and idempotency intent. */
export function useUploadBatchMutation(
  operation: 'pause' | 'cancel',
  authorizationVault: UploadAuthorizationVault,
) {
  const queryClient = useQueryClient();
  return useStandardMutation({
    requiresConfirmation: operation === 'cancel',
    isAccepted: () => operation === 'cancel',
    mutationFn: async (input: { readonly scope: IngestScope; readonly sessions: readonly UploadSession[]; readonly reason: string }): Promise<UploadBatchResult> => {
      const settled = await Promise.allSettled(input.sessions.map(async (session) => {
        if (!session.allowedActions.includes(operation === 'pause' ? 'PAUSE' : 'CANCEL')) {
          throw new Error('ACTION_NOT_ALLOWED');
        }
        if (operation === 'cancel') authorizationVault.destroy(session.uploadId);
        const wire = await mutateUploadSession({
          scope: input.scope,
          uploadId: session.uploadId,
          operation,
          body: { expected_lifecycle_status: typeof session.lifecycleStatus === 'string' ? session.lifecycleStatus : session.lifecycleStatus.raw, reason: input.reason },
          ifMatch: session.etag,
          idempotencyKey: createMutationIntentKey(),
        });
        assertResponseScope(adaptScope(wire.scope), input.scope);
        return wire;
      }));
      const succeeded = settled.filter((item) => item.status === 'fulfilled').length;
      return { attempted: settled.length, succeeded, failed: settled.length - succeeded };
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('upload-sessions') });
      void queryClient.invalidateQueries({ queryKey: resourcePrefix('upload-summary') });
    },
  });
}
