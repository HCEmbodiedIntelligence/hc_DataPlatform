import type { ActorSummary } from '../../../entities/actor';

export const fixtureActors = Object.freeze({
  admin: {
    actorId: 'usr_fx_admin',
    displayName: 'Fixture 管理员',
    roleIds: ['PROJECT_ADMIN'],
  },
  developer: {
    actorId: 'usr_fx_developer',
    displayName: 'Fixture 开发者',
    roleIds: ['PROJECT_DEVELOPER'],
  },
  processor: {
    actorId: 'usr_fx_processor',
    displayName: 'Fixture 数据处理员',
    roleIds: ['PROJECT_DATA_PROCESSOR'],
  },
} satisfies Record<string, ActorSummary>);
