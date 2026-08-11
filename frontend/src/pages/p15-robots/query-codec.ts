import { defineQueryCodec } from '../../shared/routing/route-registry';

export interface RobotsSearch {
  readonly q?: string;
  readonly robotId?: string;
  readonly componentId?: string;
  readonly tab: 'overview' | 'frames' | 'channels' | 'history';
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
}

const defaults: RobotsSearch = { tab: 'overview', limit: 20 };
const tabs = ['overview', 'frames', 'channels', 'history'] as const;

export const robotsQueryCodec = defineQueryCodec<RobotsSearch>({
  defaults,
  parse(sp) {
    const tab = tabs.includes(sp.get('tab') as RobotsSearch['tab']) ? sp.get('tab') as RobotsSearch['tab'] : defaults.tab;
    const q = sp.get('q')?.trim().slice(0, 100);
    const robotId = sp.get('robotId') || undefined;
    const componentId = robotId ? sp.get('componentId') || undefined : undefined;
    const after = sp.get('after') || undefined;
    const before = sp.get('before') || undefined;
    const rawLimit = sp.get('limit');
    const parsedLimit = rawLimit === '50' ? 50 : rawLimit === '100' ? 100 : 20;
    return { tab, limit: parsedLimit, ...(q ? { q } : {}), ...(robotId ? { robotId } : {}), ...(componentId ? { componentId } : {}), ...(after && !before ? { after } : {}), ...(before && !after ? { before } : {}) };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    for (const key of ['q', 'robotId', 'componentId', 'after', 'before'] as const) if (merged[key]) sp.set(key, merged[key]);
    if (merged.tab !== defaults.tab) sp.set('tab', merged.tab);
    if (merged.limit !== defaults.limit) sp.set('limit', String(merged.limit));
    return sp;
  },
});

