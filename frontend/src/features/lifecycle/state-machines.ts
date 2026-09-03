import type { components } from '../../shared/api/generated/platform';

export const lifecyclePolicyStates = ['DRAFT', 'ENABLED', 'PAUSED'] as const;
export type LifecyclePolicyState = components['schemas']['LifecyclePolicyState'];

const transitions: Readonly<Record<LifecyclePolicyState, readonly LifecyclePolicyState[]>> = {
  DRAFT: ['DRAFT', 'ENABLED'],
  ENABLED: ['ENABLED', 'PAUSED'],
  PAUSED: ['PAUSED', 'ENABLED'],
};

export const lifecyclePolicyStateMachine = {
  values: lifecyclePolicyStates,
  isKnown: (raw: string): raw is LifecyclePolicyState =>
    lifecyclePolicyStates.includes(raw as LifecyclePolicyState),
  canTransition: (from: LifecyclePolicyState, to: LifecyclePolicyState): boolean =>
    transitions[from].includes(to),
} as const;
