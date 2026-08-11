import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { EdlOperation } from '../../../entities/edl';
import { request } from '../../../shared/api/http-client';
import { useShellStore } from '../../../shared/scope/shell-store';
import { adaptCleaningEdl, serializeCleaningOperation } from './edl.adapter';
import { cleaningDraftKeys } from './query-keys';
import {
  commitAcceptedEnvelopeWireSchema,
  commitCleaningDraftRequestWireSchema,
  createCleaningPreviewRequestWireSchema,
  previewAcceptedEnvelopeWireSchema,
  saveCleaningEdlEnvelopeWireSchema,
} from './workbench.schemas';

function useScope() {
  const scope = useShellStore((state) => state.scope);
  return { projectId: scope?.projectId ?? null, regionCode: scope?.regionCode ?? null };
}

function path(projectId: string, regionCode: string, draftId: string, suffix: string): string {
  return `/projects/${encodeURIComponent(projectId)}/regions/${encodeURIComponent(regionCode)}/cleaning-drafts/${encodeURIComponent(draftId)}${suffix}`;
}

export interface SaveCleaningEdlIntent {
  readonly draftId: string;
  readonly etag: string;
  readonly expectedRevision: string;
  readonly expectedOperationHash: string | null;
  readonly operations: readonly EdlOperation[];
  readonly clientMutationId: string;
}

export function useSaveCleaningEdl() {
  const scope = useScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (intent: SaveCleaningEdlIntent) => {
      const body = {
        expected_edl_revision: intent.expectedRevision,
        expected_operation_hash: intent.expectedOperationHash,
        operations: intent.operations.map(serializeCleaningOperation),
        client_mutation_id: intent.clientMutationId,
      };
      const raw = await request<unknown>({
        method: 'PUT', path: path(scope.projectId!, scope.regionCode!, intent.draftId, '/edl'),
        body, ifMatch: intent.etag,
      });
      const wire = saveCleaningEdlEnvelopeWireSchema.parse(raw);
      return { draft: wire.data.draft, edl: adaptCleaningEdl(wire.data.edl), requestId: wire.request_id };
    },
    onSuccess: (_result, intent) => {
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.bootstrap(intent.draftId) });
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.detail(intent.draftId) });
    },
  });
}

export interface CreateCleaningPreviewIntent {
  readonly draftId: string;
  readonly etag: string;
  readonly baseRevisionId: string;
  readonly edlRevision: string;
  readonly operationHash: string;
  readonly idempotencyKey: string;
}

export function useCreateCleaningPreview() {
  const scope = useScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (intent: CreateCleaningPreviewIntent) => {
      const body = createCleaningPreviewRequestWireSchema.parse({
        base_revision_id: intent.baseRevisionId,
        edl_revision: intent.edlRevision,
        operation_hash: intent.operationHash,
      });
      const raw = await request<unknown>({
        method: 'POST', path: path(scope.projectId!, scope.regionCode!, intent.draftId, '/previews'),
        body, ifMatch: intent.etag, idempotencyKey: intent.idempotencyKey,
      });
      return previewAcceptedEnvelopeWireSchema.parse(raw);
    },
    onSuccess: (_result, intent) => {
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.bootstrap(intent.draftId) });
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.detail(intent.draftId) });
    },
  });
}

export interface CommitCleaningDraftIntent {
  readonly draftId: string;
  readonly etag: string;
  readonly previewId: string;
  readonly baseRevisionId: string;
  readonly edlRevision: string;
  readonly operationHash: string;
  readonly successorCompositionHash: string | null;
  readonly idempotencyKey: string;
  readonly confirmed: true;
}

export function useCommitCleaningDraft() {
  const scope = useScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (intent: CommitCleaningDraftIntent) => {
      if (intent.confirmed !== true) throw new Error('COMMIT_CONFIRMATION_REQUIRED');
      const body = commitCleaningDraftRequestWireSchema.parse({
        preview_id: intent.previewId,
        base_revision_id: intent.baseRevisionId,
        edl_revision: intent.edlRevision,
        operation_hash: intent.operationHash,
        successor_composition_hash: intent.successorCompositionHash,
        acknowledgement: { reviewed_summary: true, compared_preview: true },
      });
      const raw = await request<unknown>({
        method: 'POST', path: path(scope.projectId!, scope.regionCode!, intent.draftId, '/commits'),
        body, ifMatch: intent.etag, idempotencyKey: intent.idempotencyKey,
      });
      return commitAcceptedEnvelopeWireSchema.parse(raw);
    },
    onSuccess: (_result, intent) => {
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.bootstrap(intent.draftId) });
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.detail(intent.draftId) });
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.list({ status: 'active' }) });
      void client.invalidateQueries({ queryKey: cleaningDraftKeys.list({ status: 'committed' }) });
    },
  });
}
