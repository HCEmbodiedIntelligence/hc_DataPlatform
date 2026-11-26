export const MAX_EXPORT_ITEMS = 10_000;
export const transferWidth = (itemCount: number) => itemCount > 50 ? 4 : 1;

// Keep one bounded window, including finished blocks that await an earlier write.
export async function transferInOrder<T>(
  count: number, width: number, parent: AbortSignal,
  load: (index: number, signal: AbortSignal) => Promise<T>,
  write: (index: number, value: T, signal: AbortSignal) => Promise<void>,
) {
  const stop = new AbortController();
  const signal = AbortSignal.any([parent, stop.signal]);
  const pending = new Map<number, Promise<{ value: T } | { error: unknown }>>();
  function start(index: number) {
    if (index >= count) return;
    pending.set(index, Promise.resolve().then(() => {
      signal.throwIfAborted();
      return load(index, signal);
    }).then(value => ({ value }), error => {
      stop.abort(error);
      return { error };
    }));
  }
  try {
    for (let i = 0; i < Math.min(width, count); i++) start(i);
    for (let i = 0; i < count; i++) {
      const result = await pending.get(i)!;
      signal.throwIfAborted();
      if ("error" in result) throw result.error;
      pending.delete(i);
      await write(i, result.value, signal);
      start(i + width);
    }
  } finally {
    stop.abort();
  }
}

export function transferLabel(label: string, count: number) {
  const lanes = transferWidth(count);
  return lanes > 1 ? `${label} · 最多 ${lanes} 路并行` : label;
}
