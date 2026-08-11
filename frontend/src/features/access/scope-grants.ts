export type ScopeGrantEffect = 'ALLOW' | 'DENY';

export interface ScopeGrantInput {
  readonly effect: ScopeGrantEffect;
  readonly capabilityKeys: readonly string[];
}

export interface ScopeGrantValidation {
  readonly valid: boolean;
  readonly outsideCeiling: readonly string[];
  readonly noEffectAllow: readonly string[];
  readonly reason: string | null;
}

/** ScopeGrant can narrow an effective ceiling; it can never mint a capability. */
export function validateScopeGrant(
  input: ScopeGrantInput,
  roleCeiling: ReadonlySet<string>,
  currentlyEffective: ReadonlySet<string>,
): ScopeGrantValidation {
  const unique = [...new Set(input.capabilityKeys)].sort();
  const outsideCeiling = unique.filter((key) => !roleCeiling.has(key));
  const noEffectAllow = input.effect === 'ALLOW'
    ? unique.filter((key) => currentlyEffective.has(key))
    : [];
  let reason: string | null = null;
  if (outsideCeiling.length > 0) reason = 'ScopeGrant 超出角色 ceiling，不能扩权。';
  else if (noEffectAllow.length > 0) reason = 'V1 拒绝无效果的 ALLOW；ScopeGrant 只能收窄权限。';
  else if (input.effect === 'ALLOW') reason = 'V1 的 ALLOW 不能增加角色 base/ceiling 之外或当前未生效的能力。';
  return { valid: reason === null, outsideCeiling, noEffectAllow, reason };
}

