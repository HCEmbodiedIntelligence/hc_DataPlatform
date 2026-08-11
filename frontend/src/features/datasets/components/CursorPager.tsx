import type { CursorPageVm } from '../api';

export function CursorPager({
  pageInfo,
  snapshotAt,
  onPrevious,
  onNext,
}: Readonly<{
  pageInfo: CursorPageVm<unknown>['pageInfo'];
  snapshotAt: string;
  onPrevious: () => void;
  onNext: () => void;
}>) {
  return (
    <footer className="dataset-table-footer">
      <span>快照 {snapshotAt}</span>
      <nav className="dataset-pagination" aria-label="游标分页">
        <button
          type="button"
          className="dataset-button dataset-button--secondary"
          disabled={!pageInfo.hasPreviousPage}
          onClick={onPrevious}
        >
          上一组
        </button>
        <button
          type="button"
          className="dataset-button dataset-button--secondary"
          disabled={!pageInfo.hasNextPage}
          onClick={onNext}
        >
          下一组
        </button>
      </nav>
    </footer>
  );
}
