/**
 * The lifecycle backend contract is conditional. Keeping every state axis here makes
 * future additions fail closed instead of leaking through page-specific switches.
 */
export const lifecyclePolicyStates = ['PENDING_EFFECTIVE', 'ACTIVE', 'PAUSED', 'ERROR'] as const;
export const simulationStates = ['QUEUED', 'RUNNING', 'READY', 'EMPTY', 'FAILED', 'EXPIRED'] as const;
export const executionStates = ['QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'] as const;
export const restoreStates = ['QUEUED', 'RESTORING', 'AVAILABLE', 'EXPIRED', 'FAILED', 'CANCELLED'] as const;

export type LifecyclePolicyState = (typeof lifecyclePolicyStates)[number] | 'UNKNOWN';
export type SimulationState = (typeof simulationStates)[number] | 'UNKNOWN';
export type ExecutionState = (typeof executionStates)[number] | 'UNKNOWN';
export type RestoreState = (typeof restoreStates)[number] | 'UNKNOWN';

type KnownState<S extends string> = { readonly value: S; readonly known: true; readonly mutable: boolean };
type UnknownState = { readonly value: 'UNKNOWN'; readonly known: false; readonly mutable: false };

export type ProjectedState<S extends string> = KnownState<S> | UnknownState;

function projectState<const S extends readonly string[]>(
  values: S,
  mutableValues: ReadonlySet<S[number]>,
  raw: string,
): ProjectedState<S[number]> {
  if (!values.includes(raw)) return { value: 'UNKNOWN', known: false, mutable: false };
  const value = raw as S[number];
  return { value, known: true, mutable: mutableValues.has(value) };
}

export const lifecyclePolicyStateMachine = {
  values: lifecyclePolicyStates,
  project: (raw: string): ProjectedState<(typeof lifecyclePolicyStates)[number]> =>
    projectState(lifecyclePolicyStates, new Set<(typeof lifecyclePolicyStates)[number]>(['PENDING_EFFECTIVE', 'ACTIVE', 'PAUSED', 'ERROR']), raw),
  canTransition(from: LifecyclePolicyState, to: LifecyclePolicyState): boolean {
    if (from === 'UNKNOWN' || to === 'UNKNOWN') return false;
    const transitions: Record<(typeof lifecyclePolicyStates)[number], readonly LifecyclePolicyState[]> = {
      PENDING_EFFECTIVE: ['ACTIVE', 'PAUSED', 'ERROR'],
      ACTIVE: ['PAUSED', 'ERROR'],
      PAUSED: ['PENDING_EFFECTIVE', 'ACTIVE'],
      ERROR: ['PAUSED', 'PENDING_EFFECTIVE'],
    };
    return transitions[from].includes(to);
  },
} as const;

export const simulationStateMachine = {
  values: simulationStates,
  project: (raw: string): ProjectedState<(typeof simulationStates)[number]> =>
    projectState(simulationStates, new Set<(typeof simulationStates)[number]>(['QUEUED', 'RUNNING']), raw),
  isTerminal: (state: SimulationState): boolean =>
    state !== 'UNKNOWN' && ['READY', 'EMPTY', 'FAILED', 'EXPIRED'].includes(state),
  canAuthorizeDangerousAction: (state: SimulationState): boolean => state === 'READY' || state === 'EMPTY',
} as const;

export const executionStateMachine = {
  values: executionStates,
  project: (raw: string): ProjectedState<(typeof executionStates)[number]> =>
    projectState(executionStates, new Set<(typeof executionStates)[number]>(['QUEUED', 'RUNNING']), raw),
  isTerminal: (state: ExecutionState): boolean =>
    state !== 'UNKNOWN' && ['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(state),
  isSuccessful: (state: ExecutionState): boolean => state === 'SUCCEEDED',
} as const;

export const restoreStateMachine = {
  values: restoreStates,
  project: (raw: string): ProjectedState<(typeof restoreStates)[number]> =>
    projectState(restoreStates, new Set<(typeof restoreStates)[number]>(['QUEUED', 'RESTORING']), raw),
  isTerminal: (state: RestoreState): boolean =>
    state !== 'UNKNOWN' && ['AVAILABLE', 'EXPIRED', 'FAILED', 'CANCELLED'].includes(state),
} as const;

export interface SimulationEvidence {
  readonly status: SimulationState;
  readonly freshness: 'CURRENT' | 'STALE' | 'EXPIRED' | 'UNKNOWN';
  readonly inputHash: string;
  readonly snapshotId: string;
  readonly policySetVersion: string;
  readonly policyVersions: Readonly<Record<string, string>>;
  readonly unknownObjectCount: bigint;
  readonly blockedReasons: readonly { readonly blocking: boolean }[];
}

export interface CurrentLifecycleFacts {
  readonly inputHash: string;
  readonly snapshotId: string;
  readonly policySetVersion: string;
  readonly policyVersions: Readonly<Record<string, string>>;
}

function sameRecord(a: Readonly<Record<string, string>>, b: Readonly<Record<string, string>>): boolean {
  const ak = Object.keys(a).sort();
  const bk = Object.keys(b).sort();
  return ak.length === bk.length && ak.every((key, index) => key === bk[index] && a[key] === b[key]);
}

export function simulationAuthorizesDangerousAction(
  evidence: SimulationEvidence,
  current: CurrentLifecycleFacts,
): boolean {
  return simulationStateMachine.canAuthorizeDangerousAction(evidence.status)
    && evidence.freshness === 'CURRENT'
    && evidence.inputHash === current.inputHash
    && evidence.snapshotId === current.snapshotId
    && evidence.policySetVersion === current.policySetVersion
    && sameRecord(evidence.policyVersions, current.policyVersions)
    && evidence.unknownObjectCount === 0n
    && !evidence.blockedReasons.some((reason) => reason.blocking);
}
