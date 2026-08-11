import type { Component, ComponentTreeNode, EffectiveRelation } from '../../entities/component';

export type TopologyDiagnostic =
  | { readonly kind: 'DUPLICATE_ID'; readonly componentId: string }
  | { readonly kind: 'ORPHAN'; readonly componentId: string; readonly parentComponentId: string }
  | { readonly kind: 'CROSS_ROBOT_PARENT'; readonly componentId: string; readonly parentComponentId: string }
  | { readonly kind: 'CYCLE'; readonly componentIds: readonly string[] };

export interface ComponentTreeResult {
  readonly roots: readonly ComponentTreeNode[];
  readonly diagnostics: readonly TopologyDiagnostic[];
  readonly writable: boolean;
}

function rfc3339ToEpochNanoseconds(value: string): bigint | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, fraction = '', zone] = match;
  const millis = Date.parse(`${year}-${month}-${day}T${hour}:${minute}:${second}${zone}`);
  if (!Number.isFinite(millis)) return null;
  return BigInt(millis) * 1_000_000n + BigInt(fraction.padEnd(9, '0'));
}

export function isValidHalfOpenInterval(validFrom: string, validTo: string | null): boolean {
  const start = rfc3339ToEpochNanoseconds(validFrom);
  const end = validTo === null ? null : rfc3339ToEpochNanoseconds(validTo);
  return start !== null && (end === null || start < end);
}

export function halfOpenIntervalsOverlap(
  left: Pick<EffectiveRelation, 'validFrom' | 'validTo'>,
  right: Pick<EffectiveRelation, 'validFrom' | 'validTo'>,
): boolean {
  const leftStart = rfc3339ToEpochNanoseconds(left.validFrom);
  const rightStart = rfc3339ToEpochNanoseconds(right.validFrom);
  const leftEnd = left.validTo === null ? null : rfc3339ToEpochNanoseconds(left.validTo);
  const rightEnd = right.validTo === null ? null : rfc3339ToEpochNanoseconds(right.validTo);
  if (leftStart === null || rightStart === null || (left.validTo !== null && leftEnd === null) || (right.validTo !== null && rightEnd === null)) {
    return true;
  }
  return (rightEnd === null || leftStart < rightEnd) && (leftEnd === null || rightStart < leftEnd);
}

export function findTemporalRelationConflicts(relations: readonly EffectiveRelation[]): readonly [string, string][] {
  const conflicts: [string, string][] = [];
  for (let left = 0; left < relations.length; left += 1) {
    const a = relations[left];
    if (!a || !isValidHalfOpenInterval(a.validFrom, a.validTo)) continue;
    for (let right = left + 1; right < relations.length; right += 1) {
      const b = relations[right];
      if (!b || a.componentId !== b.componentId || a.relationType !== b.relationType) continue;
      if (halfOpenIntervalsOverlap(a, b)) conflicts.push([a.id, b.id]);
    }
  }
  return conflicts;
}

export function buildComponentTree(components: readonly Component[]): ComponentTreeResult {
  const diagnostics: TopologyDiagnostic[] = [];
  const byId = new Map<string, Component>();
  for (const component of components) {
    if (byId.has(component.id)) diagnostics.push({ kind: 'DUPLICATE_ID', componentId: component.id });
    else byId.set(component.id, component);
  }

  const childIds = new Map<string, string[]>();
  const validRootIds: string[] = [];
  for (const component of byId.values()) {
    if (component.parentComponentId === null) {
      validRootIds.push(component.id);
      continue;
    }
    const parent = byId.get(component.parentComponentId);
    if (!parent) {
      diagnostics.push({ kind: 'ORPHAN', componentId: component.id, parentComponentId: component.parentComponentId });
      continue;
    }
    if (parent.robotId !== component.robotId) {
      diagnostics.push({ kind: 'CROSS_ROBOT_PARENT', componentId: component.id, parentComponentId: parent.id });
      continue;
    }
    childIds.set(parent.id, [...(childIds.get(parent.id) ?? []), component.id]);
  }

  const visiting = new Set<string>();
  const visited = new Set<string>();
  const cyclic = new Set<string>();
  const detect = (id: string, path: readonly string[]): void => {
    if (visiting.has(id)) {
      const start = path.indexOf(id);
      const componentIds = start >= 0 ? path.slice(start) : [...path, id];
      diagnostics.push({ kind: 'CYCLE', componentIds });
      componentIds.forEach((componentId) => cyclic.add(componentId));
      return;
    }
    if (visited.has(id)) return;
    visiting.add(id);
    for (const child of childIds.get(id) ?? []) detect(child, [...path, id]);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of byId.keys()) detect(id, []);

  const createNode = (id: string): ComponentTreeNode | null => {
    const component = byId.get(id);
    if (!component || cyclic.has(id)) return null;
    const children = (childIds.get(id) ?? [])
      .map(createNode)
      .filter((value): value is ComponentTreeNode => value !== null)
      .sort((a, b) => BigInt(a.sortOrder) < BigInt(b.sortOrder) ? -1 : BigInt(a.sortOrder) > BigInt(b.sortOrder) ? 1 : a.id.localeCompare(b.id));
    return { ...component, children };
  };
  const roots = validRootIds.map(createNode).filter((value): value is ComponentTreeNode => value !== null);
  return { roots, diagnostics, writable: diagnostics.length === 0 };
}

export type SerialUniquenessScope = 'PROJECT' | 'PROJECT_REGION';

export function serialUniquenessKey(
  serialNo: string,
  scope: SerialUniquenessScope,
  projectId: string,
  regionCode: string,
): string {
  const normalized = serialNo.trim().toLocaleUpperCase('en-US');
  return scope === 'PROJECT' ? `${projectId}:${normalized}` : `${projectId}:${regionCode}:${normalized}`;
}

