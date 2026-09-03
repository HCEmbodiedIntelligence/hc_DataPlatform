export type MutationPhase =
  | 'idle'
  | 'validating'
  | 'preflighting'
  | 'confirming'
  | 'submitting'
  | 'accepted'
  | 'succeeded'
  | 'failed'
  | 'conflicted';

export interface MutationSnapshot<TResult = unknown> {
  readonly phase: MutationPhase;
  readonly intentKey: string | null;
  readonly result: TResult | null;
  readonly error: unknown;
}

const NEXT: Record<MutationPhase, readonly MutationPhase[]> = {
  idle: ['validating'],
  validating: ['preflighting', 'confirming', 'submitting', 'failed'],
  preflighting: ['confirming', 'submitting', 'failed', 'conflicted'],
  confirming: ['submitting', 'idle'],
  submitting: ['accepted', 'succeeded', 'failed', 'conflicted'],
  accepted: ['succeeded', 'failed', 'conflicted'],
  succeeded: ['idle'],
  failed: ['validating', 'submitting', 'idle'],
  conflicted: ['validating', 'idle'],
};

export function transitionMutation<TResult>(
  state: MutationSnapshot<TResult>,
  phase: MutationPhase,
  patch: Partial<Omit<MutationSnapshot<TResult>, 'phase'>> = {},
): MutationSnapshot<TResult> {
  if (!NEXT[state.phase].includes(phase)) {
    throw new Error(`Invalid mutation transition ${state.phase} -> ${phase}`);
  }
  return { ...state, ...patch, phase };
}

export function createMutationIntentKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `intent-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function createIdleMutation<TResult>(): MutationSnapshot<TResult> {
  return { phase: 'idle', intentKey: null, result: null, error: null };
}
