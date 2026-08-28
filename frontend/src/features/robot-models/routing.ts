export const routes = {
  robotModels: {
    build(
      search: {
        readonly modelId?: string;
        readonly versionId?: string;
        readonly detailTab?: string;
        readonly targetRobotId?: string;
        readonly targetRegionCode?: string;
      } = {},
    ): string {
      const query = new URLSearchParams();
      if (search.modelId) query.set("modelId", search.modelId);
      if (search.versionId) query.set("versionId", search.versionId);
      if (search.detailTab) query.set("detailTab", search.detailTab);
      if (search.targetRobotId)
        query.set("targetRobotId", search.targetRobotId);
      if (search.targetRegionCode)
        query.set("targetRegionCode", search.targetRegionCode);
      const encoded = query.toString();
      return encoded
        ? `/settings/robot-models?${encoded}`
        : "/settings/robot-models";
    },
  },
} as const;
