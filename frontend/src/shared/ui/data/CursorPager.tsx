import { Button } from 'antd';

export interface CursorPageInfo {
  startCursor: string | null;
  endCursor: string | null;
  hasPreviousPage: boolean;
  hasNextPage: boolean;
}

export type CursorRequest =
  | Readonly<{ before: string; after?: never }>
  | Readonly<{ after: string; before?: never }>;

export interface CursorPagerProps {
  pageInfo: CursorPageInfo;
  onChange: (request: CursorRequest) => void;
  busy?: boolean;
  windowLabel?: string;
}

function usableCursor(cursor: string | null): cursor is string {
  return cursor !== null && cursor.trim().length > 0;
}

export function CursorPager({
  pageInfo,
  onChange,
  busy = false,
  windowLabel = '当前数据窗口',
}: CursorPagerProps) {
  const previousCursor =
    pageInfo.hasPreviousPage && usableCursor(pageInfo.startCursor) ? pageInfo.startCursor : null;
  const nextCursor =
    pageInfo.hasNextPage && usableCursor(pageInfo.endCursor) ? pageInfo.endCursor : null;

  return (
    <nav aria-label="游标分页" data-pagination-contract="after-before">
      <Button
        disabled={busy || previousCursor === null}
        onClick={() => {
          if (previousCursor !== null) onChange({ before: previousCursor });
        }}
      >
        上一组
      </Button>
      <span aria-live="polite">{windowLabel}</span>
      <Button
        disabled={busy || nextCursor === null}
        onClick={() => {
          if (nextCursor !== null) onChange({ after: nextCursor });
        }}
      >
        下一组
      </Button>
    </nav>
  );
}
