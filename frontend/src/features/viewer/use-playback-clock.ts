import { useEffect, useMemo, useRef } from "react";
import { createPlaybackClock, type PlaybackClock } from "./PlaybackClock";

/** Preserve the clock through Strict Mode effect replay, dispose on real departure. */
export function usePlaybackClock(
  startNs: string,
  endNs: string,
  identity: string,
): PlaybackClock {
  const clock = useMemo(
    () => createPlaybackClock({ startNs, endNs }),
    [startNs, endNs, identity],
  );
  const disposalTokens = useRef(new Map<PlaybackClock, symbol>());
  useEffect(() => {
    const token = Symbol("viewer-clock-lifecycle");
    const tokens = disposalTokens.current;
    tokens.set(clock, token);
    return () => {
      queueMicrotask(() => {
        if (tokens.get(clock) !== token) return;
        clock.dispose();
        tokens.delete(clock);
      });
    };
  }, [clock]);
  return clock;
}
