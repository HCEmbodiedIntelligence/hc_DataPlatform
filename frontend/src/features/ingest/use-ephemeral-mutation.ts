import { useCallback, useEffect, useRef, useState } from 'react';
import { createIdleMutation, transitionMutation, type MutationSnapshot } from './mutation-machine';

interface EphemeralMutationCallbacks<TData> {
  readonly onSuccess?: (data: TData) => void;
  readonly onError?: (error: Error) => void;
  readonly onSettled?: () => void;
}

interface EphemeralMutationOptions<TData, TVariables> {
  readonly mutationFn: (variables: TVariables) => Promise<TData>;
  readonly intentKeyOf?: (variables: TVariables) => string | null;
  readonly requiresPreflight?: boolean;
  readonly requiresConfirmation?: boolean;
  readonly isAccepted?: (data: TData) => boolean;
  readonly onSuccess?: (data: TData, variables: TVariables) => void;
  readonly onSettled?: () => void;
}

function prepare<TData>(current: MutationSnapshot<TData>): MutationSnapshot<TData> {
  let next = current;
  if (next.phase === 'accepted') next = transitionMutation(next, 'succeeded');
  if (['succeeded', 'failed', 'conflicted'].includes(next.phase)) next = transitionMutation(next, 'idle', { result: null, error: null, intentKey: null });
  return next;
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error('INGEST_MUTATION_FAILED');
}

function isConflict(error: Error): boolean {
  const code = 'code' in error ? (error as Error & { readonly code?: unknown }).code : undefined;
  return typeof code === 'string' && /CONFLICT|PRECONDITION|ETAG|VERSION/u.test(code);
}

/**
 * Executes secret/File-bearing writes without TanStack MutationCache. Variables live only in
 * the current promise closure and therefore cannot appear in Query persistence or DevTools.
 */
export function useEphemeralMutation<TData, TVariables>(options: EphemeralMutationOptions<TData, TVariables>) {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const mounted = useRef(true);
  const running = useRef(false);
  const [lifecycle, setLifecycle] = useState<MutationSnapshot<TData>>(() => createIdleMutation<TData>());
  const [data, setData] = useState<TData | undefined>();
  const [error, setError] = useState<Error | null>(null);
  const [isPending, setPending] = useState(false);
  useEffect(() => () => { mounted.current = false; }, []);

  const execute = useCallback(async (variables: TVariables, callbacks: EphemeralMutationCallbacks<TData> = {}): Promise<TData> => {
    if (running.current) throw new Error('INGEST_MUTATION_ALREADY_PENDING');
    running.current = true;
    if (mounted.current) {
      setPending(true);
      setError(null);
      setLifecycle((current) => {
        let next = transitionMutation(prepare(current), 'validating', { intentKey: optionsRef.current.intentKeyOf?.(variables) ?? null, result: null, error: null });
        if (optionsRef.current.requiresPreflight) next = transitionMutation(next, 'preflighting');
        if (optionsRef.current.requiresConfirmation) next = transitionMutation(next, 'confirming');
        return transitionMutation(next, 'submitting');
      });
    }
    try {
      const result = await optionsRef.current.mutationFn(variables);
      if (mounted.current) {
        setData(result);
        setLifecycle((current) => transitionMutation(current, optionsRef.current.isAccepted?.(result) ? 'accepted' : 'succeeded', { result, error: null }));
      }
      optionsRef.current.onSuccess?.(result, variables);
      callbacks.onSuccess?.(result);
      return result;
    } catch (caught) {
      const failure = asError(caught);
      if (mounted.current) {
        setError(failure);
        setLifecycle((current) => transitionMutation(current, isConflict(failure) ? 'conflicted' : 'failed', { error: failure }));
      }
      callbacks.onError?.(failure);
      throw failure;
    } finally {
      running.current = false;
      if (mounted.current) setPending(false);
      optionsRef.current.onSettled?.();
      callbacks.onSettled?.();
    }
  }, []);

  const mutate = useCallback((variables: TVariables, callbacks?: EphemeralMutationCallbacks<TData>) => {
    void execute(variables, callbacks).catch(() => undefined);
  }, [execute]);

  const reset = useCallback(() => {
    if (running.current) return;
    setData(undefined);
    setError(null);
    setLifecycle(createIdleMutation<TData>());
  }, []);

  return { mutate, mutateAsync: execute, reset, data, error, isPending, isError: error !== null, isSuccess: data !== undefined && error === null, lifecycle };
}
