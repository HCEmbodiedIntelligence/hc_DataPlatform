import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { configureRuntime } from '../../src/shared/config/runtime';
import { useShellStore } from '../../src/shared/scope/shell-store';
import { jobQueryKey, useAsyncJob, type AsyncJob } from '../../src/shared/jobs/use-async-job';

interface EventSourceLike {
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onerror: (() => void) | null;
  close: () => void;
}

const eventSources: EventSourceLike[] = [];

class FixtureEventSource implements EventSourceLike {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(url: string) {
    void url;
    eventSources.push(this);
  }
}

function jobWire(resourceVersion: string, status = 'RUNNING') {
  return {
    job_id: 'job_fx_01',
    job_type: 'UPLOAD_VERIFY',
    status,
    resource_type: 'upload_session',
    resource_id: 'upl_fx_01',
    progress: null,
    result_ref: null,
    error: null,
    created_at: '2026-08-05T08:00:00Z',
    updated_at: '2026-08-05T08:00:02Z',
    resource_version: resourceVersion,
  };
}

describe('useAsyncJob version ordering', () => {
  beforeEach(() => {
    eventSources.length = 0;
    vi.stubGlobal('EventSource', FixtureEventSource);
    configureRuntime({ apiBaseUrl: 'https://api.fixture.invalid', sseBaseUrl: 'https://sse.fixture.invalid', buildVersion: 'web-test', releaseEnv: 'test' });
    useShellStore.getState().setScope({ organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' });
  });

  it('does not let an older SSE event overwrite a newer query snapshot', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const snapshot: AsyncJob = {
      jobId: 'job_fx_01',
      jobType: 'UPLOAD_VERIFY',
      status: 'RUNNING',
      resourceType: 'upload_session',
      resourceId: 'upl_fx_01',
      progress: null,
      resultRef: null,
      error: null,
      createdAt: '2026-08-05T08:00:00Z',
      updatedAt: '2026-08-05T08:00:02Z',
      resourceVersion: '5',
    };
    queryClient.setQueryData(jobQueryKey('job_fx_01'), snapshot);
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => useAsyncJob('job_fx_01'), { wrapper });
    await waitFor(() => {
      if (result.current.error) throw result.current.error;
      expect(result.current.data?.resourceVersion).toBe('5');
    });
    const source = eventSources.at(-1);
    expect(source).toBeDefined();
    act(() => {
      source?.onmessage?.({ data: JSON.stringify({
        event_id: 'evt_fx_old',
        event_type: 'job.updated',
        scope_key: 'org_fx_01/prj_fx_01/cn-shanghai',
        resource_type: 'job',
        resource_id: 'job_fx_01',
        resource_version: '4',
        occurred_at: '2026-08-05T08:00:01Z',
        payload: jobWire('4'),
      }) });
    });
    await waitFor(() => expect(result.current.data?.resourceVersion).toBe('5'));
  });

  it('refetches the authoritative snapshot for a newer notification-only event', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    queryClient.setQueryData(jobQueryKey('job_fx_01'), {
      jobId: 'job_fx_01', jobType: 'UPLOAD_VERIFY', status: 'RUNNING',
      resourceType: 'upload_session', resourceId: 'upl_fx_01', progress: null,
      resultRef: null, error: null, createdAt: '2026-08-05T08:00:00Z',
      updatedAt: '2026-08-05T08:00:02Z', resourceVersion: '5',
    } satisfies AsyncJob);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
    renderHook(() => useAsyncJob('job_fx_01'), { wrapper });
    await waitFor(() => expect(eventSources.at(-1)).toBeDefined());
    act(() => {
      eventSources.at(-1)?.onmessage?.({ data: JSON.stringify({
        event_id: 'evt_fx_new',
        event_type: 'job.updated',
        scope_key: 'org_fx_01/prj_fx_01/cn-shanghai',
        resource_type: 'job',
        resource_id: 'job_fx_01',
        resource_version: '6',
        occurred_at: '2026-08-05T08:00:03Z',
        payload: {},
      }) });
    });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: jobQueryKey('job_fx_01'), exact: true }));
  });
});
