import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getRuntimeConfig } from '../../../shared/config/runtime';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { registerSseCleanup } from '../../../shared/api/transport-lifecycle';
import { useShellStore } from '../../../shared/scope/shell-store';
import type { UploadSession } from './model';
import { compareResourceVersion } from './model';

type StreamState = 'idle' | 'connected' | 'reconnecting' | 'polling';

function eventUrl(scopeKey: string, lastEventId: string | null): string {
  const base = getRuntimeConfig().sseBaseUrl.replace(/\/$/u, '');
  const query = new URLSearchParams({ scope_key: scopeKey, resource_type: 'UPLOAD_SESSION' });
  if (lastEventId) query.set('last_event_id', lastEventId);
  return `${base}/ingest/events?${query}`;
}

/** One scope stream accelerates list snapshots; 5s query polling remains the authority/fallback. */
export function useUploadProgressStream(items: readonly UploadSession[], enabled: boolean): StreamState {
  const scopeKey = useShellStore((state) => state.scopeKey);
  const queryClient = useQueryClient();
  const versions = useRef(new Map<string, string>());
  const lastEventId = useRef<string | null>(null);
  const seen = useRef(new Set<string>());
  const [state, setState] = useState<StreamState>('idle');

  useEffect(() => {
    for (const item of items) {
      const current = versions.current.get(item.uploadId);
      if (!current || compareResourceVersion(item.resourceVersion, current) > 0) versions.current.set(item.uploadId, item.resourceVersion);
    }
  }, [items]);

  useEffect(() => {
    versions.current.clear();
    lastEventId.current = null;
    seen.current.clear();
  }, [scopeKey]);

  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') {
      setState(enabled ? 'polling' : 'idle');
      return undefined;
    }
    let source: EventSource | null = null;
    let disposed = false;
    let attempts = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const requestRefresh = () => {
      if (refreshTimer !== null) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        const prefix = makeQueryKey('ingest', 'upload-sessions', {}).slice(0, 3);
        void queryClient.invalidateQueries({ queryKey: prefix });
      }, 250);
    };
    const close = () => {
      source?.close();
      source = null;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      reconnectTimer = null;
      refreshTimer = null;
    };
    const connect = () => {
      if (disposed) return;
      setState('reconnecting');
      source = new EventSource(eventUrl(scopeKey, lastEventId.current));
      source.onopen = () => { attempts = 0; setState('connected'); };
      source.onmessage = (message) => {
        try {
          if (typeof message.data !== 'string') return;
          const value: unknown = JSON.parse(message.data);
          if (typeof value !== 'object' || value === null) return;
          const parsed = value as Record<string, unknown>;
          if (parsed.scope_key !== undefined && parsed.scope_key !== scopeKey) return;
          if (parsed.resource_type !== 'UPLOAD_SESSION' || typeof parsed.resource_id !== 'string' || typeof parsed.resource_version !== 'string') return;
          if (typeof parsed.event_id === 'string') {
            if (seen.current.has(parsed.event_id)) return;
            seen.current.add(parsed.event_id);
            lastEventId.current = parsed.event_id;
          }
          const current = versions.current.get(parsed.resource_id);
          if (current && compareResourceVersion(parsed.resource_version, current) <= 0) return;
          versions.current.set(parsed.resource_id, parsed.resource_version);
          requestRefresh();
        } catch {
          // Invalid or out-of-order events cannot update server snapshots.
        }
      };
      source.onerror = () => {
        source?.close();
        source = null;
        attempts += 1;
        if (attempts >= 3) { setState('polling'); return; }
        setState('reconnecting');
        reconnectTimer = setTimeout(connect, Math.min(1_000 * 2 ** (attempts - 1), 10_000));
      };
    };
    connect();
    const unregister = registerSseCleanup(close);
    return () => { disposed = true; unregister(); close(); };
  }, [enabled, queryClient, scopeKey]);

  return state;
}
