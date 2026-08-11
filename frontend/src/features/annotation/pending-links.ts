import { request } from '../../shared/api/http-client';
import {
  createManualIssueCommand,
  routes as cleaningOwnerRoutes,
} from '../cleaning/routing';
import type { CreateManualIssueCommandInput } from '../cleaning/routing';

/**
 * P08's only ManualIssue bridge. The Owner command builds and validates the request;
 * this module only adapts its absolute contract path to the shared HTTP base URL.
 * It intentionally exposes no CleaningDraft command or P11 route.
 */
export async function reportManualIssueFromAnnotation(input: CreateManualIssueCommandInput) {
  return createManualIssueCommand(input, (command) => request<unknown>({
    method: command.method,
    path: command.path.replace(/^\/api\/v1(?=\/)/, ''),
    ...(command.body !== undefined ? { body: command.body } : {}),
    idempotencyKey: command.headers['Idempotency-Key'],
    ...(command.signal ? { signal: command.signal } : {}),
  }));
}

export function buildManualIssuesLink(input: {
  readonly datasetId: string;
  readonly versionId: string;
  readonly episodeId: string;
  readonly issueId?: string;
  readonly returnTo?: string;
}): string {
  return cleaningOwnerRoutes.manualIssues.build(input);
}

export { createManualIssueCommand, cleaningOwnerRoutes };
export type { CreateManualIssueCommandInput } from '../cleaning/routing';
