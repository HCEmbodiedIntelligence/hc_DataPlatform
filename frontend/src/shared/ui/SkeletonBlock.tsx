export interface SkeletonBlockProps {
  width: number | string;
  height: number | string;
  label?: string;
}

export function SkeletonBlock({ width, height, label = '正在加载' }: SkeletonBlockProps) {
  return (
    <span className="skeleton-block" role="status" aria-label={label} style={{ width, height }}>
      <span className="sr-only">{label}</span>
    </span>
  );
}
