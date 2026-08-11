export const routes = {
  robotModels: {
    build(search: { readonly modelId?: string; readonly versionId?: string; readonly detailTab?: string } = {}): string {
      const query = new URLSearchParams();
      if (search.modelId) query.set('modelId', search.modelId);
      if (search.versionId) query.set('versionId', search.versionId);
      if (search.detailTab) query.set('detailTab', search.detailTab);
      const encoded = query.toString();
      return encoded ? `/settings/robot-models?${encoded}` : '/settings/robot-models';
    },
  },
} as const;

