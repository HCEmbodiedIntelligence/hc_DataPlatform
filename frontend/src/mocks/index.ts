import { configureOwnerSignedAnnotationCapabilities } from '../features/annotation/capabilities';
import { applyScenarioFromUrl, setScenario } from './scenarios/registry';

export async function startBrowserMocks(): Promise<void> {
  const { startBrowserWorker } = await import('./browser');
  await startBrowserWorker();
  configureOwnerSignedAnnotationCapabilities(['annotation_task.rebase', 'annotation_draft.edit', 'annotation_set.read']);
  const scenarioApplied = await applyScenarioFromUrl();
  if (!scenarioApplied) {
    // A bare local URL should be immediately usable. The management happy
    // scenario supplies the fixture principal, scope and full capability
    // snapshot while each domain keeps its own happy data scenario.
    await setScenario('management', 'happy');
  }
}

export { registerScenario, setScenario } from './scenarios/registry';
