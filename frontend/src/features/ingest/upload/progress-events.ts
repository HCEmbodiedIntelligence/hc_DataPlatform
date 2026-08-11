import type { UploadSession } from './model';
import { acceptNewerResource } from './model';

export interface UploadProgressEvent {
  readonly eventId: string;
  readonly scopeKey: string;
  readonly resourceId: string;
  readonly resourceVersion: string;
  readonly safeSnapshot: UploadSession;
}

export function mergeUploadProgressEvent(
  current: UploadSession,
  event: UploadProgressEvent,
  expectedScopeKey: string,
  seenEventIds: Set<string>,
): UploadSession {
  if (event.scopeKey !== expectedScopeKey || event.resourceId !== current.uploadId || seenEventIds.has(event.eventId)) return current;
  seenEventIds.add(event.eventId);
  return acceptNewerResource(current, event.safeSnapshot);
}
