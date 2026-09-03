type Cleanup = () => void | Promise<void>;

const requestControllers = new Set<AbortController>();
const sseCleanups = new Set<Cleanup>();
const resourceCleanups = new Map<string, Set<Cleanup>>();

export function createManagedAbortController(
  signal?: AbortSignal,
  options: { cancelOnScopeChange?: boolean } = {},
): {
  controller: AbortController;
  release: () => void;
} {
  const controller = new AbortController();
  const cancelOnScopeChange = options.cancelOnScopeChange ?? true;
  const forwardAbort = () => controller.abort(signal?.reason);
  if (signal?.aborted) forwardAbort();
  else signal?.addEventListener('abort', forwardAbort, { once: true });
  if (cancelOnScopeChange) requestControllers.add(controller);
  return {
    controller,
    release: () => {
      if (cancelOnScopeChange) requestControllers.delete(controller);
      signal?.removeEventListener('abort', forwardAbort);
    },
  };
}

export function registerSseCleanup(cleanup: Cleanup): () => void {
  sseCleanups.add(cleanup);
  return () => sseCleanups.delete(cleanup);
}

export type ScopedResourceKind =
  | 'signed-url'
  | 'media'
  | 'worker'
  | 'object-url'
  | 'webgl'
  | 'sensitive-memory';

export function registerScopedCleanup(kind: ScopedResourceKind, cleanup: Cleanup): () => void {
  const bucket = resourceCleanups.get(kind) ?? new Set<Cleanup>();
  bucket.add(cleanup);
  resourceCleanups.set(kind, bucket);
  return () => bucket.delete(cleanup);
}

async function runCleanups(cleanups: Iterable<Cleanup>): Promise<void> {
  await Promise.allSettled(
    [...cleanups].map(async (cleanup) => {
      try {
        await cleanup();
      } catch {
        // Continue releasing independent resources after a single cleanup failure.
      }
    }),
  );
}

export async function cancelActiveTransports(): Promise<void> {
  for (const controller of requestControllers) controller.abort('scope-change');
  requestControllers.clear();
  await runCleanups(sseCleanups);
  sseCleanups.clear();
}

export async function releaseScopedResources(): Promise<void> {
  for (const cleanups of resourceCleanups.values()) await runCleanups(cleanups);
  resourceCleanups.clear();
}
