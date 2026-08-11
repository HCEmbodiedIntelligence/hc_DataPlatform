export const routes = {
  robots: {
    build(search: { readonly robotId?: string; readonly componentId?: string; readonly tab?: string } = {}): string {
      const query = new URLSearchParams();
      if (search.robotId) query.set('robotId', search.robotId);
      if (search.componentId) query.set('componentId', search.componentId);
      if (search.tab) query.set('tab', search.tab);
      const encoded = query.toString();
      return encoded ? `/settings/robots?${encoded}` : '/settings/robots';
    },
  },
} as const;

