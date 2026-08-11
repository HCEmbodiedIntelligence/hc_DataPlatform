import { describe, expect, it, vi } from 'vitest';
import { applyScenarioFromUrl, registerScenario, REQUIRED_SCENARIOS, setScenario } from '../../src/mocks/scenarios/registry';
import { FIXTURE_BASE_TIME, fixtureAsyncJobs, fixtureScope } from '../../src/mocks/fixtures/common';

describe('shared mock foundation', () => {
  it('registers namespaced scenarios for direct tests and URL selection', async () => {
    const first = vi.fn();
    const second = vi.fn();
    const unregisterFirst = registerScenario('fixture-a', 'happy', first);
    const unregisterSecond = registerScenario('fixture-b', 'empty', second);
    await setScenario('fixture-a', 'happy');
    expect(first).toHaveBeenCalledOnce();
    await expect(applyScenarioFromUrl('https://application.invalid/?mockScenario=fixture-b:empty')).resolves.toBe(true);
    expect(second).toHaveBeenCalledOnce();
    unregisterFirst();
    unregisterSecond();
  });

  it('keeps common fixtures deterministic and ID-prefixed', () => {
    expect(FIXTURE_BASE_TIME).toBe('2026-08-05T08:00:00Z');
    expect(fixtureScope.organizationId).toMatch(/^org_fx_/u);
    expect(fixtureScope.projectId).toMatch(/^prj_fx_/u);
    expect(Object.keys(fixtureAsyncJobs)).toEqual(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN']);
    expect(Object.values(fixtureAsyncJobs).every((job) => job.jobId.startsWith('job_fx_'))).toBe(true);
    expect(REQUIRED_SCENARIOS).toContain('gone');
    expect(REQUIRED_SCENARIOS).toContain('scope-switch-race');
  });
});
