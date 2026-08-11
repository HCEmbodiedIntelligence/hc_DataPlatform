import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { getRuntimeConfig } from '../config/runtime';
import { request } from '../api/http-client';
import { makeQueryKey, type SharedQueryKey } from '../api/query-keys';
import { registerSseCleanup } from '../api/transport-lifecycle';
import { parseWire } from '../api/validate';
import { useShellStore } from '../scope/shell-store';
import type { AsyncJob, JobStatus } from './types';

export type { AsyncJob, JobStatus } from './types';

export interface AsyncJobEvent {
  eventId: string;
  resourceVersion: string;
  occurredAt: string;
  job: AsyncJob;
}

export type JobConnectionStatus = 'idle' | 'connected' | 'reconnecting' | 'polling';

const jobStatusSchema = z.enum(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN']);
const asyncJobWireSchema = z
  .object({
    job_id: z.string().min(1),
    job_type: z.string().min(1),
    status: jobStatusSchema,
    resource_type: z.string().min(1),
    resource_id: z.string().min(1),
    progress: z.unknown().nullable(),
    result_ref: z.unknown().nullable(),
    error: z.unknown().nullable(),
    created_at: z.iso.datetime({ offset: true }),
    updated_at: z.iso.datetime({ offset: true }),
    resource_version: z.string().regex(/^\d+$/u),
  })
  .strict();

const asyncJobResponseSchema = z.union([
  asyncJobWireSchema,
  z.object({ job: asyncJobWireSchema, request_id: z.string().optional() }).passthrough(),
]);

const asyncJobEventWireSchema = z
  .object({
    event_id: z.string().min(1),
    event_type: z.string().min(1),
    scope_key: z.string().min(1),
    resource_type: z.literal('job'),
    resource_id: z.string().min(1),
    resource_version: z.string().regex(/^\d+$/u),
    occurred_at: z.iso.datetime({ offset: true }),
    payload: z.unknown(),
  })
  .passthrough();

type AsyncJobWire = z.infer<typeof asyncJobWireSchema>;

function adaptJob(wire: AsyncJobWire): AsyncJob {
  return {
    jobId: wire.job_id,
    jobType: wire.job_type,
    status: wire.status,
    resourceType: wire.resource_type,
    resourceId: wire.resource_id,
    progress: wire.progress,
    resultRef: wire.result_ref,
    error: wire.error,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    resourceVersion: wire.resource_version,
  };
}

function isTerminal(status: JobStatus): boolean {
  return status === 'SUCCEEDED' || status === 'FAILED' || status === 'CANCELLED';
}

function compareVersions(left: string, right: string): number {
  try {
    const leftVersion = BigInt(left);
    const rightVersion = BigInt(right);
    return leftVersion < rightVersion ? -1 : leftVersion > rightVersion ? 1 : 0;
  } catch {
    return left.localeCompare(right);
  }
}

export function mergeAsyncJob(current: AsyncJob | undefined, incoming: AsyncJob): AsyncJob {
  if (current === undefined) return incoming;
  return compareVersions(incoming.resourceVersion, current.resourceVersion) > 0 ? incoming : current;
}

export function jobQueryKey(jobId: string): SharedQueryKey {
  return makeQueryKey('jobs', 'job', jobId);
}

function sseUrl(jobId: string, scopeKey: string, lastEventId: string | null): string {
  const base = getRuntimeConfig().sseBaseUrl.replace(/\/$/u, '');
  const params = new URLSearchParams({ scope_key: scopeKey });
  if (lastEventId) params.set('cursor', lastEventId);
  return `${base}/jobs/${encodeURIComponent(jobId)}/events?${params.toString()}`;
}

function parseSseJob(raw: unknown): AsyncJob | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const payload = raw as Record<string, unknown>;
  const candidate = payload.job ?? payload;
  const result = asyncJobWireSchema.safeParse(candidate);
  if (!result.success) return null;
  return adaptJob(result.data);
}

export function useAsyncJob(jobId: string) {
  const queryClient = useQueryClient();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const key = useMemo(() => {
    void scopeKey;
    return jobQueryKey(jobId);
  }, [jobId, scopeKey]);
  const [connectionStatus, setConnectionStatus] = useState<JobConnectionStatus>('idle');
  const reconnectAttempts = useRef(0);
  const lastEventId = useRef<string | null>(null);
  const seenEventIds = useRef(new Set<string>());

  const query = useQuery({
    queryKey: key,
    enabled: jobId.length > 0,
    queryFn: async ({ signal }) => {
      const endpoint = `/jobs/${encodeURIComponent(jobId)}`;
      const raw = await request<unknown>({ method: 'GET', path: endpoint, signal });
      const parsed = parseWire(asyncJobResponseSchema, raw, {
        endpoint,
        schemaVersion: 'AsyncJob.v1',
        requestId:
          typeof raw === 'object' && raw !== null && 'request_id' in raw
            ? String((raw as { request_id: unknown }).request_id)
            : null,
      });
      return adaptJob('job' in parsed ? parsed.job : parsed);
    },
    refetchInterval: ({ state }) => {
      const job = state.data;
      if (job && isTerminal(job.status)) return false;
      return connectionStatus === 'polling' ? 5_000 : 15_000;
    },
    retry: 2,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 10_000),
  });

  const terminal = query.data ? isTerminal(query.data.status) : false;

  useEffect(() => {
    lastEventId.current = null;
    seenEventIds.current.clear();
    reconnectAttempts.current = 0;
  }, [jobId, scopeKey]);

  useEffect(() => {
    if (!jobId || terminal) {
      setConnectionStatus('idle');
      return undefined;
    }
    if (typeof EventSource === 'undefined') {
      setConnectionStatus('polling');
      return undefined;
    }

    let disposed = false;
    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const close = () => {
      source?.close();
      source = null;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };

    const connect = () => {
      if (disposed) return;
      setConnectionStatus('reconnecting');
      source = new EventSource(sseUrl(jobId, scopeKey, lastEventId.current), {
        withCredentials: true,
      });
      source.onopen = () => {
        reconnectAttempts.current = 0;
        setConnectionStatus('connected');
      };
      source.onmessage = (message) => {
        try {
          if (typeof message.data !== 'string') return;
          const raw = JSON.parse(message.data) as unknown;
          const parsedEvent = asyncJobEventWireSchema.safeParse(raw);
          if (!parsedEvent.success) return;
          const event = parsedEvent.data;
          if (event.scope_key !== scopeKey || event.resource_id !== jobId) return;
          if (seenEventIds.current.has(event.event_id)) return;
          seenEventIds.current.add(event.event_id);
          lastEventId.current = event.event_id;
          if (seenEventIds.current.size > 500) {
            const oldest = seenEventIds.current.values().next().value;
            if (typeof oldest === 'string') seenEventIds.current.delete(oldest);
          }
          const current = queryClient.getQueryData<AsyncJob>(key);
          if (
            current !== undefined &&
            compareVersions(event.resource_version, current.resourceVersion) <= 0
          ) {
            return;
          }
          const incoming = parseSseJob(event.payload);
          if (
            incoming !== null &&
            compareVersions(incoming.resourceVersion, event.resource_version) >= 0
          ) {
            queryClient.setQueryData<AsyncJob>(key, (snapshot) => mergeAsyncJob(snapshot, incoming));
          } else {
            void queryClient.invalidateQueries({ queryKey: key, exact: true });
          }
        } catch {
          // Invalid event payloads cannot update the query snapshot.
        }
      };
      source.onerror = () => {
        source?.close();
        source = null;
        reconnectAttempts.current += 1;
        if (reconnectAttempts.current >= 3) {
          setConnectionStatus('polling');
          void queryClient.invalidateQueries({ queryKey: key, exact: true });
          return;
        }
        setConnectionStatus('reconnecting');
        const delay = Math.min(1_000 * 2 ** (reconnectAttempts.current - 1), 10_000);
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();
    const unregister = registerSseCleanup(close);
    return () => {
      disposed = true;
      unregister();
      close();
    };
  }, [jobId, key, queryClient, scopeKey, terminal]);

  return { ...query, connectionStatus };
}
