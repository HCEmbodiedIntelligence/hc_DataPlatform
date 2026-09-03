import type { RequestHandler } from 'msw';

type HandlerModule = { default?: readonly RequestHandler[] };

const modules = import.meta.glob<HandlerModule>('./*.handlers.ts', { eager: true });

/* Library handler generics use `any` internally; response data is not exposed from this aggregator. */
/* eslint-disable @typescript-eslint/no-unsafe-assignment, @typescript-eslint/no-unsafe-return */
export const handlers: RequestHandler[] = Object.entries(modules)
  .sort(([left], [right]) => left.localeCompare(right))
  .flatMap(([, module]) => (Array.isArray(module.default) ? [...module.default] : []));

export default handlers;
/* eslint-enable @typescript-eslint/no-unsafe-assignment, @typescript-eslint/no-unsafe-return */
