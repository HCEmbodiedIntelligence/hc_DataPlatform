import { useEffect, useRef, useState } from 'react';

/** React text updates are limited to at most 10Hz; canvas/transport progress stays independent. */
export function useThrottledValue<T>(value: T, minimumIntervalMs = 100): T {
  const [visible, setVisible] = useState(value);
  const lastCommit = useRef(0);
  const pending = useRef(value);

  useEffect(() => {
    pending.current = value;
    const elapsed = performance.now() - lastCommit.current;
    const delay = Math.max(0, minimumIntervalMs - elapsed);
    const timer = window.setTimeout(() => {
      lastCommit.current = performance.now();
      setVisible(pending.current);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [minimumIntervalMs, value]);

  return visible;
}
