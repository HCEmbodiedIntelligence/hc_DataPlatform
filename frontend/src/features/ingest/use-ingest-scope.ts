import type { IngestScope } from '../../entities/data-source';
import { useShellStore } from '../../shared/scope/shell-store';

export function useIngestScope(): IngestScope | null {
  const scope = useShellStore((state) => state.scope);
  if (!scope?.projectId || !scope.regionCode) return null;
  return {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  };
}
