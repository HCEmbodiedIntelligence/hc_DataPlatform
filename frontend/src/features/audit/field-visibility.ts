export type AuditCapabilityCheck = (capability: string) => boolean;

export type AuditFieldVisibility = Readonly<{
  actorRoleIds: boolean;
  requestMetadata: boolean;
  changeValues: boolean;
  integrityEvidence: boolean;
  exportControls: boolean;
}>;

/**
 * Field visibility is derived only from granted capabilities. Project role IDs
 * are historical audit facts and must never be used as authorization inputs.
 */
export function resolveAuditFieldVisibility(has: AuditCapabilityCheck): AuditFieldVisibility {
  if (!has('audit.read')) {
    return {
      actorRoleIds: false,
      requestMetadata: false,
      changeValues: false,
      integrityEvidence: false,
      exportControls: false,
    };
  }
  return {
    actorRoleIds: has('access.read'),
    requestMetadata: has('access.read') || has('audit.export'),
    changeValues: has('access.read') || has('dataset_version.read'),
    integrityEvidence: has('audit.export'),
    exportControls: has('audit.export'),
  };
}
