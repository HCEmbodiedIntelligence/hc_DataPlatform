import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from 'react';

const focusableSelector =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function trapTabKey(
  event: ReactKeyboardEvent,
  containerRef: RefObject<HTMLElement | null>,
): void {
  if (event.key !== 'Tab') return;
  const elements = [...(containerRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])];
  const first = elements[0];
  const last = elements.at(-1);
  if (!first || !last) return;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
