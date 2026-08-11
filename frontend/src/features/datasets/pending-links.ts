import { routes as cleaningRoutes } from '../cleaning/routing';

/** P07 only navigates to the successor Draft created by the atomic server transaction. */
export function buildSuccessorDraftPendingLink(draftId: string): string {
  return cleaningRoutes.cleaningWorkbench.build({ draftId });
}
