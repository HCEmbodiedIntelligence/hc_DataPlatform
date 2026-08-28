import { useEffect, useState } from 'react';

export interface PlaybackClock {
  readonly startNs: string;
  readonly endNs: string;
  currentNs(): string;
  isPlaying(): boolean;
  playbackRate(): number;
  subscribe(fn: (ns: string, update: PlaybackClockUpdate) => void): () => void;
  play(): void;
  pause(): void;
  seek(ns: string): void;
  setRate(r: number): void;
  dispose(): void;
}

export interface PlaybackClockUpdate {
  readonly reason: 'subscribe' | 'tick' | 'play' | 'pause' | 'seek' | 'rate' | 'ended';
  readonly playing: boolean;
  readonly rate: number;
}

type FrameHandle = ReturnType<typeof setTimeout> | number;

const decimalNs = /^(0|[1-9][0-9]*)$/;

function parseNs(value: string, field: string): bigint {
  if (!decimalNs.test(value)) throw new RangeError(`${field} must be an unsigned decimal nanosecond string`);
  return BigInt(value);
}

function monotonicNow(): number {
  return typeof performance === 'undefined' ? Date.now() : performance.now();
}

function requestFrame(fn: (at: number) => void): FrameHandle {
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(fn);
  return setTimeout(() => fn(monotonicNow()), 16);
}

function cancelFrame(handle: FrameHandle | null): void {
  if (handle === null) return;
  if (typeof cancelAnimationFrame === 'function' && typeof handle === 'number') cancelAnimationFrame(handle);
  else clearTimeout(handle);
}

export function createPlaybackClock(opts: { startNs: string; endNs: string }): PlaybackClock {
  const start = parseNs(opts.startNs, 'startNs');
  const end = parseNs(opts.endNs, 'endNs');
  if (start >= end) throw new RangeError('PlaybackClock uses a non-empty [startNs, endNs) interval');

  let current = start;
  let rate = 1;
  let playing = false;
  let disposed = false;
  let frame: FrameHandle | null = null;
  let previousAt = monotonicNow();
  const listeners = new Set<(ns: string, update: PlaybackClockUpdate) => void>();

  const emit = (reason: PlaybackClockUpdate['reason']) => {
    const value = current.toString();
    const update = { reason, playing, rate } as const;
    for (const listener of [...listeners]) listener(value, update);
  };

  const stopFrame = () => {
    cancelFrame(frame);
    frame = null;
  };

  const schedule = () => {
    if (!playing || disposed || frame !== null) return;
    frame = requestFrame(tick);
  };

  const tick = (at: number) => {
    frame = null;
    if (!playing || disposed) return;
    const elapsedMs = Math.max(0, at - previousAt);
    previousAt = at;
    const deltaNs = BigInt(Math.max(0, Math.round(elapsedMs * 1_000_000 * rate)));
    const next = current + deltaNs;
    if (next >= end) {
      current = end - 1n;
      playing = false;
    } else {
      current = next;
    }
    emit(playing ? 'tick' : 'ended');
    schedule();
  };

  return {
    startNs: start.toString(),
    endNs: end.toString(),
    currentNs: () => current.toString(),
    isPlaying: () => playing,
    playbackRate: () => rate,
    subscribe(fn) {
      if (disposed) return () => undefined;
      listeners.add(fn);
      fn(current.toString(), { reason: 'subscribe', playing, rate });
      return () => listeners.delete(fn);
    },
    play() {
      if (disposed || playing) return;
      if (current >= end - 1n) current = start;
      playing = true;
      previousAt = monotonicNow();
      emit('play');
      schedule();
    },
    pause() {
      if (disposed) return;
      playing = false;
      stopFrame();
      emit('pause');
    },
    seek(ns) {
      if (disposed) return;
      const requested = parseNs(ns, 'seek ns');
      current = requested < start ? start : requested >= end ? end - 1n : requested;
      previousAt = monotonicNow();
      emit('seek');
    },
    setRate(nextRate) {
      if (!Number.isFinite(nextRate) || nextRate <= 0 || nextRate > 16) {
        throw new RangeError('Playback rate must be finite and within (0, 16]');
      }
      rate = nextRate;
      previousAt = monotonicNow();
      emit('rate');
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      playing = false;
      stopFrame();
      listeners.clear();
    },
  };
}

export function useClockText(clock: PlaybackClock, hz = 10): string {
  const safeHz = Number.isFinite(hz) && hz > 0 ? Math.min(hz, 10) : 10;
  const [text, setText] = useState(() => clock.currentNs());

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let pending = clock.currentNs();
    let lastCommitAt = 0;
    const intervalMs = 1000 / safeHz;
    const commit = () => {
      timer = null;
      lastCommitAt = monotonicNow();
      setText(pending);
    };
    const unsubscribe = clock.subscribe((ns) => {
      pending = ns;
      const remaining = intervalMs - (monotonicNow() - lastCommitAt);
      if (remaining <= 0) commit();
      else if (timer === null) timer = setTimeout(commit, remaining);
    });
    return () => {
      unsubscribe();
      if (timer !== null) clearTimeout(timer);
    };
  }, [clock, safeHz]);

  return text;
}
