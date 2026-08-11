/** T2 owns the P12 route builder. Replace this adapter once its public export lands. */
export const storageOverviewPendingLink = {
  build(): string {
    return '/storage/overview';
  },
  dependency: 'T2 routes.storageOverview builder',
} as const;

