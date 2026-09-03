import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { createIdleMutation, transitionMutation, type MutationSnapshot } from './mutation-machine';

interface StandardMutationOptions<TData, TVariables> {
  readonly mutationFn: (variables: TVariables) => Promise<TData>;
  readonly intentKeyOf?: (variables: TVariables) => string | null;
  readonly requiresPreflight?: boolean;
  readonly requiresConfirmation?: boolean;
  readonly isAccepted?: (data: TData) => boolean;
  readonly onSuccess?: (data: TData, variables: TVariables) => void;
  readonly onSettled?: () => void;
}

function prepareForNextIntent<TData>(current: MutationSnapshot<TData>): MutationSnapshot<TData> {
  let next = current;
  if (next.phase === 'accepted') next = transitionMutation(next, 'succeeded');
  if (['succeeded', 'failed', 'conflicted'].includes(next.phase)) next = transitionMutation(next, 'idle', { result: null, error: null, intentKey: null });
  return next;
}

function isConflict(error: unknown): boolean {
  if (typeof error !== 'object' || error === null || !('code' in error)) return false;
  const code = (error as { readonly code?: unknown }).code;
  return typeof code === 'string' && /CONFLICT|PRECONDITION|ETAG|VERSION/u.test(code);
}

/** Central lifecycle used by every ingest write; React Query remains the network executor only. */
export function useStandardMutation<TData, TVariables>(options: StandardMutationOptions<TData, TVariables>) {
  const [lifecycle, setLifecycle] = useState<MutationSnapshot<TData>>(() => createIdleMutation<TData>());
  const mutation = useMutation<TData, Error, TVariables>({
    mutationFn: async (variables) => {
      setLifecycle((current) => {
        let next = transitionMutation(prepareForNextIntent(current), 'validating', {
          intentKey: options.intentKeyOf?.(variables) ?? null,
          result: null,
          error: null,
        });
        if (options.requiresPreflight) next = transitionMutation(next, 'preflighting');
        if (options.requiresConfirmation) next = transitionMutation(next, 'confirming');
        return transitionMutation(next, 'submitting');
      });
      try {
        return await options.mutationFn(variables);
      } catch (error) {
        setLifecycle((current) => transitionMutation(current, isConflict(error) ? 'conflicted' : 'failed', { error }));
        throw error;
      }
    },
    onSuccess: (data, variables) => {
      setLifecycle((current) => transitionMutation(current, options.isAccepted?.(data) ? 'accepted' : 'succeeded', { result: data, error: null }));
      options.onSuccess?.(data, variables);
    },
    onSettled: () => options.onSettled?.(),
  });
  return { ...mutation, lifecycle };
}
