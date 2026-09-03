export interface ActorSummary {
  actorId: string;
  displayName: string;
  avatarUrl?: string;
  roleIds: readonly ProjectRoleId[];
}

export type ProjectRoleId = 'PROJECT_ADMIN' | 'PROJECT_DEVELOPER' | 'PROJECT_DATA_PROCESSOR';
