export interface LifecycleRouteSearch {
  readonly tab?: 'policies' | 'executions' | 'restores' | 'multipart';
  readonly policyId?: string;
  readonly simulationId?: string;
  readonly executionId?: string;
  readonly restoreTaskId?: string;
  readonly uploadId?: string;
}

const build = (search: LifecycleRouteSearch = {}): string => {
  const query = new URLSearchParams();
  if (search.tab && search.tab !== 'policies') query.set('tab', search.tab);
  for (const key of ['policyId', 'simulationId', 'executionId', 'restoreTaskId', 'uploadId'] as const) {
    if (search[key]) query.set(key, search[key]);
  }
  const encoded = query.toString();
  return encoded ? `/storage/lifecycle?${encoded}` : '/storage/lifecycle';
};

export const routes = {
  lifecycle: { build },
  storageLifecycle: { build },
} as const;
