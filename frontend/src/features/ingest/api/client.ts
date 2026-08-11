import { request } from '../../../shared/api/http-client';
import { parseWire } from '../../../shared/api/validate';
import type { z, ZodType } from 'zod';
import type { IngestScope } from '../../../entities/data-source';
import { assertNoCredentialLeak } from '../connectors/credential-boundary';
import {
  connectionTestJobEnvelopeWireSchema,
  dataSourceEnvelopeWireSchema,
  dataSourcePageWireSchema,
  uploadHandoffEnvelopeWireSchema,
  uploadEventPageWireSchema,
  uploadCreationOptionsEnvelopeWireSchema,
  uploadJobEnvelopeWireSchema,
  uploadObjectPageWireSchema,
  uploadSessionBootstrapWireSchema,
  uploadSessionEnvelopeWireSchema,
  uploadSessionPageWireSchema,
  verificationRunPageWireSchema,
  verificationRunJobEnvelopeWireSchema,
} from './wire-schemas';
import type {
  ConnectionTestJobEnvelopeWire,
  DataSourceEnvelopeWire,
  UploadHandoffEnvelopeWire,
  UploadJobEnvelopeWire,
  UploadSessionBootstrapWire,
  UploadSessionEnvelopeWire,
  VerificationRunJobEnvelopeWire,
} from './wire-schemas';

export interface CursorFilters {
  readonly after?: string;
  readonly before?: string;
  readonly limit: number;
  readonly sort?: string;
  readonly snapshotAt?: string;
  readonly [key: string]: unknown;
}

function resourceRoot(scope: IngestScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}`;
}

function ordinary<S extends ZodType>(schema: S, raw: unknown, endpoint: string): z.output<S> {
  assertNoCredentialLeak(raw);
  return parseWire(schema, raw, { endpoint, schemaVersion: 'ingest.v1alpha1' }) as z.output<S>;
}

/** Secret envelopes are validated without traversing, logging, caching, or exposing their values. */
function secretEnvelope<S extends ZodType>(schema: S, raw: unknown, endpoint: string): z.output<S> {
  return parseWire(schema, raw, { endpoint, schemaVersion: 'ingest.v1alpha1' }) as z.output<S>;
}

export async function getDataSourcesPage(scope: IngestScope, query: Record<string, unknown>, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/data-sources/page`;
  const raw = await request<unknown>({ method: 'GET', path, query, signal });
  return ordinary(dataSourcePageWireSchema, raw, path);
}

export async function getDataSource(scope: IngestScope, sourceId: string, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/data-sources/${encodeURIComponent(sourceId)}`;
  const raw = await request<unknown>({ method: 'GET', path, signal });
  return ordinary(dataSourceEnvelopeWireSchema, raw, path);
}

export async function listUploadSessions(scope: IngestScope, query: Record<string, unknown>, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/upload-sessions`;
  const raw = await request<unknown>({ method: 'GET', path, query, signal });
  return ordinary(uploadSessionPageWireSchema, raw, path);
}

export async function getUploadCreationOptions(scope: IngestScope, query: Record<string, unknown>, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/upload-sessions:creation-options`;
  const raw = await request<unknown>({ method: 'GET', path, query, signal });
  return ordinary(uploadCreationOptionsEnvelopeWireSchema, raw, path);
}

export async function getUploadSessionBootstrap(scope: IngestScope, uploadId: string, signal?: AbortSignal): Promise<UploadSessionBootstrapWire> {
  const path = `${resourceRoot(scope)}/upload-sessions/${encodeURIComponent(uploadId)}/bootstrap`;
  const raw = await request<unknown>({ method: 'GET', path, signal });
  return ordinary(uploadSessionBootstrapWireSchema, raw, path);
}

export async function listUploadObjects(scope: IngestScope, uploadId: string, query: Record<string, unknown>, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/upload-sessions/${encodeURIComponent(uploadId)}/objects`;
  const raw = await request<unknown>({ method: 'GET', path, query, signal });
  return ordinary(uploadObjectPageWireSchema, raw, path);
}

export async function listVerificationRuns(scope: IngestScope, uploadId: string, query: Record<string, unknown>, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/upload-sessions/${encodeURIComponent(uploadId)}/verification-runs`;
  const raw = await request<unknown>({ method: 'GET', path, query, signal });
  return ordinary(verificationRunPageWireSchema, raw, path);
}

export async function listUploadEvents(scope: IngestScope, uploadId: string, query: Record<string, unknown>, signal?: AbortSignal) {
  const path = `${resourceRoot(scope)}/upload-sessions/${encodeURIComponent(uploadId)}/events`;
  const raw = await request<unknown>({ method: 'GET', path, query, signal });
  return ordinary(uploadEventPageWireSchema, raw, path);
}

type DataSourceOperation = 'create' | 'update' | 'rotate-credential' | 'test-connection' | 'enable' | 'disable';

export async function mutateDataSource<T extends DataSourceOperation>(options: {
  readonly scope: IngestScope;
  readonly sourceId?: string;
  readonly operation: T;
  readonly body: unknown;
  readonly idempotencyKey: string;
  readonly ifMatch?: string;
}): Promise<T extends 'test-connection' ? ConnectionTestJobEnvelopeWire : DataSourceEnvelopeWire> {
  const { scope, sourceId, operation } = options;
  const base = `${resourceRoot(scope)}/data-sources`;
  const path = operation === 'create'
    ? base
    : operation === 'update'
      ? `${base}/${encodeURIComponent(sourceId ?? '')}`
      : `${base}/${encodeURIComponent(sourceId ?? '')}:${operation}`;
  const raw = await request<unknown>({
    method: operation === 'update' ? 'PATCH' : 'POST',
    path,
    body: options.body,
    idempotencyKey: options.idempotencyKey,
    ifMatch: options.ifMatch,
  });
  return (operation === 'test-connection'
    ? ordinary(connectionTestJobEnvelopeWireSchema, raw, path)
    : ordinary(dataSourceEnvelopeWireSchema, raw, path)) as T extends 'test-connection'
      ? ConnectionTestJobEnvelopeWire
      : DataSourceEnvelopeWire;
}

type UploadSessionOperation = 'create' | 'pause' | 'resume' | 'retry-verification' | 'cancel';
type UploadSessionMutationResult<T extends UploadSessionOperation> =
  T extends 'create' | 'resume' ? UploadHandoffEnvelopeWire
    : T extends 'retry-verification' ? VerificationRunJobEnvelopeWire
      : T extends 'cancel' ? UploadJobEnvelopeWire
        : UploadSessionEnvelopeWire;

export async function mutateUploadSession<T extends UploadSessionOperation>(options: {
  readonly scope: IngestScope;
  readonly uploadId?: string;
  readonly operation: T;
  readonly body: unknown;
  readonly idempotencyKey: string;
  readonly ifMatch?: string;
}): Promise<UploadSessionMutationResult<T>> {
  const base = `${resourceRoot(options.scope)}/upload-sessions`;
  const path = options.operation === 'create'
    ? base
    : `${base}/${encodeURIComponent(options.uploadId ?? '')}:${options.operation}`;
  const raw = await request<unknown>({
    method: 'POST',
    path,
    body: options.body,
    idempotencyKey: options.idempotencyKey,
    ifMatch: options.ifMatch,
  });
  if (options.operation === 'create' || options.operation === 'resume') {
    return secretEnvelope(uploadHandoffEnvelopeWireSchema, raw, path) as UploadSessionMutationResult<T>;
  }
  if (options.operation === 'retry-verification') return ordinary(verificationRunJobEnvelopeWireSchema, raw, path) as UploadSessionMutationResult<T>;
  if (options.operation === 'cancel') return ordinary(uploadJobEnvelopeWireSchema, raw, path) as UploadSessionMutationResult<T>;
  return ordinary(uploadSessionEnvelopeWireSchema, raw, path) as UploadSessionMutationResult<T>;
}
