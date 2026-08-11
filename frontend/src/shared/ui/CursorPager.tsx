import type { PageInfo } from '../api/pagination';

export interface CursorPagerProps {
  pageInfo: PageInfo;
  onPrevious: (cursor: string) => void;
  onNext: (cursor: string) => void;
  disabled?: boolean;
}

export function CursorPager({ pageInfo, onPrevious, onNext, disabled = false }: CursorPagerProps) {
  const canPrevious = pageInfo.has_previous_page && pageInfo.start_cursor !== null;
  const canNext = pageInfo.has_next_page && pageInfo.end_cursor !== null;
  return (
    <nav className="cursor-pager" aria-label="游标分页">
      <button type="button" disabled={disabled || !canPrevious} onClick={() => pageInfo.start_cursor && onPrevious(pageInfo.start_cursor)}>
        上一组
      </button>
      <span aria-live="polite">当前数据窗口</span>
      <button type="button" disabled={disabled || !canNext} onClick={() => pageInfo.end_cursor && onNext(pageInfo.end_cursor)}>
        下一组
      </button>
    </nav>
  );
}
