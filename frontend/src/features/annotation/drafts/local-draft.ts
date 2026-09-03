import { useEffect } from 'react';
import type { AnnotationFormDefinition } from '../../../entities/annotation-schema';

export const LOCAL_ANNOTATION_DRAFT_TTL_MS = 2 * 60 * 60 * 1000;
const PREFIX = 'hc:annotation:recovery:v1:';

export interface LocalDraftIdentity {
  readonly projectId: string;
  readonly regionCode: string;
  readonly taskId: string;
  readonly taskEtag: string;
  readonly baselineRevision: number;
}

interface StoredDraft {
  readonly identity: LocalDraftIdentity;
  readonly expiresAt: number;
  readonly values: Readonly<Record<string, string | number | boolean | readonly string[] | null>>;
}

function isStoredValue(value: unknown): value is string | number | boolean | readonly string[] | null {
  return value === null
    || typeof value === 'string'
    || (typeof value === 'number' && Number.isFinite(value))
    || typeof value === 'boolean'
    || (Array.isArray(value) && value.every((entry) => typeof entry === 'string'));
}

function isStoredDraft(value: unknown): value is StoredDraft {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Readonly<Record<string, unknown>>;
  if (typeof candidate.expiresAt !== 'number' || !Number.isFinite(candidate.expiresAt) || !candidate.identity || typeof candidate.identity !== 'object' || !candidate.values || typeof candidate.values !== 'object' || Array.isArray(candidate.values)) return false;
  const identity = candidate.identity as Readonly<Record<string, unknown>>;
  return typeof identity.projectId === 'string'
    && typeof identity.regionCode === 'string'
    && typeof identity.taskId === 'string'
    && typeof identity.taskEtag === 'string'
    && Number.isInteger(identity.baselineRevision)
    && Object.entries(candidate.values as Readonly<Record<string, unknown>>).every(([field, entry]) => !['__proto__', 'constructor', 'prototype'].includes(field) && isStoredValue(entry));
}

function key(identity: LocalDraftIdentity): string { return `${PREFIX}${identity.projectId}:${identity.regionCode}:${identity.taskId}`; }

function storage(): Storage | null {
  try { return typeof sessionStorage === 'undefined' ? null : sessionStorage; } catch { return null; }
}

function sanitize(
  definition: AnnotationFormDefinition,
  values: Readonly<Record<string, unknown>>,
): StoredDraft['values'] {
  const result: Record<string, string | number | boolean | readonly string[] | null> = {};
  for (const field of definition.fields) {
    if (!field.localCache) continue;
    const value = values[field.key];
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || value === null) result[field.key] = value;
    else if (Array.isArray(value) && value.every((entry) => typeof entry === 'string')) result[field.key] = value;
  }
  return result;
}

export function saveLocalAnnotationDraft(identity: LocalDraftIdentity, definition: AnnotationFormDefinition, values: Readonly<Record<string, unknown>>, now = Date.now()): void {
  const target = storage();
  if (!target) return;
  const payload: StoredDraft = { identity, expiresAt: now + LOCAL_ANNOTATION_DRAFT_TTL_MS, values: sanitize(definition, values) };
  try { target.setItem(key(identity), JSON.stringify(payload)); } catch { /* Recovery storage is best-effort. */ }
}

export function loadLocalAnnotationDraft(identity: LocalDraftIdentity, now = Date.now()): StoredDraft['values'] | null {
  const target = storage();
  const raw = target?.getItem(key(identity));
  if (!target || !raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isStoredDraft(parsed) || parsed.expiresAt <= now || JSON.stringify(parsed.identity) !== JSON.stringify(identity)) {
      target.removeItem(key(identity));
      return null;
    }
    return parsed.values;
  } catch {
    try { target.removeItem(key(identity)); } catch { /* Recovery storage is best-effort. */ }
    return null;
  }
}

export function clearLocalAnnotationDraft(identity: LocalDraftIdentity): void {
  try { storage()?.removeItem(key(identity)); } catch { /* Recovery storage is best-effort. */ }
}

export function useLocalAnnotationDraftAutosave(identity: LocalDraftIdentity | null, definition: AnnotationFormDefinition | null, values: Readonly<Record<string, unknown>>, dirty: boolean): void {
  useEffect(() => {
    if (!identity || !definition || !dirty) return;
    const timer = setTimeout(() => saveLocalAnnotationDraft(identity, definition, values), 750);
    return () => clearTimeout(timer);
  }, [definition, dirty, identity, values]);
}

export function useUnsavedChangesWarning(dirty: boolean): void {
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    addEventListener('beforeunload', warn);
    return () => removeEventListener('beforeunload', warn);
  }, [dirty]);
}
