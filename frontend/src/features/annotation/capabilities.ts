export const annotationCapabilities = [
  'annotation_task.read',
  'annotation_task.claim',
  'annotation_task.create',
  'annotation_task.assign',
  'annotation_task.rebase',
  'annotation.edit',
  'annotation_draft.edit',
  'annotation.save',
  'annotation.submit',
  'annotation.review',
  'annotation_set.read',
] as const;

export type AnnotationCapability = typeof annotationCapabilities[number];

export const pendingOwnerCapabilities = new Set<AnnotationCapability>([
  'annotation_task.rebase',
  'annotation_draft.edit',
  'annotation_set.read',
]);

let ownerSignedCapabilities: ReadonlySet<string> = new Set();

export function configureOwnerSignedAnnotationCapabilities(capabilities: readonly AnnotationCapability[]): () => void {
  const previous = ownerSignedCapabilities;
  ownerSignedCapabilities = new Set(capabilities);
  return () => { ownerSignedCapabilities = previous; };
}

export function getOwnerSignedAnnotationCapabilities(): ReadonlySet<string> { return ownerSignedCapabilities; }

export interface CapabilityDecision {
  readonly state: 'allowed' | 'forbidden' | 'feature-unavailable';
  readonly reason?: string;
}

export function decideAnnotationCapability(
  capabilities: ReadonlySet<string> | null | undefined,
  required: AnnotationCapability,
  signedCapabilities: ReadonlySet<string> = ownerSignedCapabilities,
): CapabilityDecision {
  if (!capabilities) return { state: 'feature-unavailable', reason: '授权状态不可用' };
  if (!annotationCapabilities.includes(required)) return { state: 'feature-unavailable', reason: '未知 capability' };
  if (pendingOwnerCapabilities.has(required) && !signedCapabilities.has(required)) {
    return { state: 'feature-unavailable', reason: '能力角色分配尚未由 Owner 签署' };
  }
  return capabilities.has(required) ? { state: 'allowed' } : { state: 'forbidden', reason: '当前授权不包含所需能力' };
}
