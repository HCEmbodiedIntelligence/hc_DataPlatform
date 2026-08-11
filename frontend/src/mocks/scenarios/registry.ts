export type ScenarioApply = () => void | (() => void) | Promise<void | (() => void)>;

export const REQUIRED_SCENARIOS = [
  'happy',
  'empty',
  'filtered-empty',
  'first-loading',
  'partial-error',
  'fatal-error',
  'forbidden',
  'not-found',
  'gone',
  'conflict',
  'rate-limited',
  'offline-recovery',
  'unknown-enum',
  'contract-mismatch',
  'scope-switch-race',
] as const;

const registry = new Map<string, Map<string, ScenarioApply>>();
let cleanup: (() => void) | null = null;

export function registerScenario(domain: string, name: string, apply: ScenarioApply): () => void {
  if (!domain.trim() || !name.trim()) throw new Error('Scenario domain and name are required');
  const scenarios = registry.get(domain) ?? new Map<string, ScenarioApply>();
  scenarios.set(name, apply);
  registry.set(domain, scenarios);
  return () => {
    scenarios.delete(name);
    if (scenarios.size === 0) registry.delete(domain);
  };
}

export async function setScenario(domain: string, name: string): Promise<void> {
  const apply = registry.get(domain)?.get(name);
  if (apply === undefined) throw new Error(`Unknown mock scenario: ${domain}:${name}`);
  cleanup?.();
  cleanup = null;
  const nextCleanup = await apply();
  if (typeof nextCleanup === 'function') cleanup = nextCleanup;
}

export async function applyScenarioFromUrl(url = globalThis.location?.href): Promise<boolean> {
  if (!url) return false;
  const value = new URL(url, 'https://application.invalid').searchParams.get('mockScenario');
  if (!value) return false;
  const separator = value.indexOf(':');
  if (separator <= 0 || separator === value.length - 1) return false;
  await setScenario(value.slice(0, separator), value.slice(separator + 1));
  return true;
}

export function resetScenario(): void {
  cleanup?.();
  cleanup = null;
}
