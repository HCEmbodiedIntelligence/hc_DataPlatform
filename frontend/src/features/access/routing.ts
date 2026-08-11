export const routes = {
  access: {
    build(search: { readonly tab?: 'members' | 'roles' | 'policies'; readonly memberId?: string } = {}): string {
      const query = new URLSearchParams();
      if (search.tab && search.tab !== 'members') query.set('tab', search.tab);
      if (search.memberId) query.set('memberId', search.memberId);
      const encoded = query.toString();
      return encoded ? `/settings/access?${encoded}` : '/settings/access';
    },
  },
} as const;

