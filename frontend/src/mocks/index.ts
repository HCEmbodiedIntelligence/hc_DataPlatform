import { configureOwnerSignedAnnotationCapabilities } from "../features/annotation/capabilities";
import { useShellStore } from "../shared/scope/shell-store";
import { applyScenarioFromUrl, setScenario } from "./scenarios/registry";

function installBrowserFixtureSessionScope(): void {
  const shell = useShellStore.getState();
  if (
    !shell.sessionToken ||
    !shell.scope?.projectId ||
    shell.sessionScopes.length > 0
  )
    return;

  shell.setSessionScopes(
    [
      {
        projectId: shell.scope.projectId,
        regionCodes: shell.scope.regionCode ? [shell.scope.regionCode] : [],
        projectWide: !shell.scope.regionCode,
        capabilities: shell.authorization?.capabilities ?? [],
      },
    ],
    1,
  );
}

export async function startBrowserMocks(): Promise<void> {
  const { startBrowserWorker } = await import("./browser");
  await startBrowserWorker();
  configureOwnerSignedAnnotationCapabilities([
    "annotation_task.rebase",
    "annotation_draft.edit",
    "annotation_set.read",
  ]);
  const scenarioApplied = await applyScenarioFromUrl();
  if (!scenarioApplied) {
    // A bare local URL should be immediately usable. The management happy
    // scenario supplies the fixture principal, scope and full capability
    // snapshot while each domain keeps its own happy data scenario.
    await setScenario("management", "happy");
  }
  // Browser fixtures are deliberately isolated from the real-route session
  // bootstrap. Keep their explicit mock scope complete so RuntimePlatformShell
  // does not try to call an unmocked authentication endpoint.
  installBrowserFixtureSessionScope();
}

export { registerScenario, setScenario } from "./scenarios/registry";
