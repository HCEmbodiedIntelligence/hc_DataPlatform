import { useEffect, useState } from 'react';

export const compactLayoutQuery = '(max-width: 767px)';

export function useCompactLayout(): boolean {
  const [compact, setCompact] = useState(
    () => typeof window !== 'undefined' && window.matchMedia?.(compactLayoutQuery).matches === true,
  );

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const media = window.matchMedia(compactLayoutQuery);
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  return compact;
}
