import { makeScopeKey } from '../../entities/scope';
import { configureOwnerSignedAnnotationCapabilities } from '../../features/annotation/capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import { registerScenario } from './registry';

export const ANNOTATION_SCENARIOS = [
  'happy', 'empty', 'filtered-empty', 'first-loading', 'refreshing', 'partial-error', 'fatal-error', 'forbidden', 'not-found', 'gone', 'conflict', 'rate-limited', 'offline-recovery', 'unknown-enum', 'contract-mismatch', 'scope-switch-race',
  'signed-url-expired', 'sse-disconnected', 'partial-media-error', 'validation-error', 'submitting', 'accepted-job', 'etag-conflict', 'permission-revoked', 'stale',
  'asset-handoff-existing', 'asset-handoff-claimable', 'asset-handoff-create', 'asset-handoff-assigned-other', 'asset-handoff-forbidden',
  'single-arm-7-axis-camera', 'single-arm-6-axis-camera-pointcloud', 'dual-arm-14-axis-multicam', 'variable-axis-mismatch', 'pointcloud-preview-pending', 'unknown-modality',
] as const;

export type AnnotationScenario = typeof ANNOTATION_SCENARIOS[number];
let active: AnnotationScenario = 'happy';
export function setAnnotationScenario(scenario: AnnotationScenario): void { active = scenario; }
export function getAnnotationScenario(): AnnotationScenario {
  if (typeof location !== 'undefined') {
    const raw = new URLSearchParams(location.search).get('mockScenario');
    const requested = raw?.includes(':') ? raw.split(':').at(-1) : raw;
    if (requested && (ANNOTATION_SCENARIOS as readonly string[]).includes(requested)) return requested as AnnotationScenario;
  }
  return active;
}
export function resetAnnotationScenario(): void { active = 'happy'; }

const scope = { organizationId: 'org_fx_01', projectId: 'prj_fx_01', regionCode: 'cn-shanghai' } as const;
const capabilities = [
  'annotation_task.read', 'annotation_task.claim', 'annotation_task.create', 'annotation_task.assign', 'annotation_task.rebase',
  'annotation.edit', 'annotation_draft.edit', 'annotation.save', 'annotation.submit', 'annotation.review', 'annotation_set.read',
  'episode.read', 'manual_issue.read', 'manual_issue.create',
] as const;

for (const scenario of ANNOTATION_SCENARIOS) {
  registerScenario('annotation', scenario, () => {
    setAnnotationScenario(scenario);
    const shell = useShellStore.getState();
    shell.setSession({ actorId: 'usr_fx_developer', displayName: 'Fixture 标注开发者', roleIds: ['PROJECT_DEVELOPER'] }, 'fixture-bearer');
    shell.setScope(scope);
    shell.setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: 'role_fx_annotation_unsigned_01',
      capabilities: scenario === 'forbidden' || scenario === 'permission-revoked' ? [] : capabilities,
      fetchedAt: '2026-08-05T08:00:00Z',
    });
    // Explicit test-only owner decision; production defaults remain fail closed.
    const restoreSignature = configureOwnerSignedAnnotationCapabilities(['annotation_task.rebase', 'annotation_draft.edit', 'annotation_set.read']);
    return () => { restoreSignature(); resetAnnotationScenario(); };
  });
}
