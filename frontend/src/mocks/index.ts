import { applyScenarioFromUrl } from './scenarios/registry';

export async function startBrowserMocks(): Promise<void> {
  const { startBrowserWorker } = await import('./browser');
  await startBrowserWorker();
  await applyScenarioFromUrl();
}

export { registerScenario, setScenario } from './scenarios/registry';
