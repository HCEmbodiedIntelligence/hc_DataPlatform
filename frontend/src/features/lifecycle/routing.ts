export interface LifecycleRouteSearch {
  readonly tab?: 'policies' | 'audit';
  readonly cursor?: string;
  readonly limit?: 20 | 50 | 100;
}

const build = (search: LifecycleRouteSearch = {}): string => {
  const query = new URLSearchParams();
  if (search.tab === 'audit') query.set('tab', 'audit');
  if (search.cursor) query.set('cursor', search.cursor);
  if (search.limit && search.limit !== 50) query.set('limit', String(search.limit));
  const encoded = query.toString();
  return encoded ? `/storage/lifecycle?${encoded}` : '/storage/lifecycle';
};

export const routes = {
  lifecycle: { build },
  storageLifecycle: { build },
} as const;
