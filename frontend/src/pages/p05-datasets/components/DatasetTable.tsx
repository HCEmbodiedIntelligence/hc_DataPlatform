import type { DatasetId } from '../../../entities/dataset';
import type { CursorPageVm, DatasetListItemVm } from '../../../features/datasets/api';

function actionAllowed(item: DatasetListItemVm, action: string): boolean {
  return item.allowedActions.some((candidate) => candidate.action === action && candidate.allowed);
}

export function DatasetTable({
  page,
  refreshing,
  canReadEpisodes,
  onOpen,
  onOpenEpisodes,
  onPrevious,
  onNext,
}: Readonly<{
  page: CursorPageVm<DatasetListItemVm>;
  refreshing: boolean;
  canReadEpisodes: boolean;
  onOpen: (datasetId: DatasetId) => void;
  onOpenEpisodes: (item: DatasetListItemVm) => void;
  onPrevious: () => void;
  onNext: () => void;
}>) {
  return (
    <section className="dataset-table-shell" aria-busy={refreshing} aria-label="数据集结果">
      {refreshing ? (
        <div className="dataset-refresh-indicator" role="status">
          正在刷新当前游标窗口…
        </div>
      ) : null}
      <div className="dataset-table-scroll">
        <table className="dataset-table">
          <thead>
            <tr>
              <th>数据集</th>
              <th>当前 Ready</th>
              <th>Episodes</th>
              <th>待复核</th>
              <th>已退回</th>
              <th>可处理草稿</th>
              <th>活动时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((item) => (
              <tr key={item.datasetId} data-dataset-id={item.datasetId}>
                <td data-label="数据集">
                  <button
                    className="dataset-link"
                    type="button"
                    onClick={() => onOpen(item.datasetId)}
                  >
                    <strong>{item.name}</strong>
                    <small>{item.datasetId}</small>
                  </button>
                </td>
                <td data-label="当前 Ready">
                  {item.currentVersion ? (
                    <>
                      <strong>{item.currentVersion.displayVersion}</strong>
                      <small>{item.currentVersion.versionId}</small>
                    </>
                  ) : (
                    <span className="dataset-status dataset-status--unknown">待导入</span>
                  )}
                </td>
                <td data-label="Episodes">{item.episodeCount}</td>
                <td data-label="待复核">{item.pendingReviewVersionCount}</td>
                <td data-label="已退回">{item.returnedVersionCount}</td>
                <td data-label="可处理草稿">{item.actionableDraftCount}</td>
                <td data-label="活动时间">{new Date(item.datasetActivityAt).toLocaleString()}</td>
                <td data-label="操作">
                  <div className="dataset-actions">
                    <button
                      type="button"
                      className="dataset-link"
                      disabled={!actionAllowed(item, 'OPEN_DATASET')}
                      onClick={() => onOpen(item.datasetId)}
                    >
                      打开
                    </button>
                    {item.currentVersion &&
                    canReadEpisodes &&
                    actionAllowed(item, 'OPEN_EPISODE') ? (
                      <button
                        type="button"
                        className="dataset-link"
                        onClick={() => onOpenEpisodes(item)}
                      >
                        Episodes
                      </button>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer className="dataset-table-footer">
        <span>快照 {page.snapshotAt} · 稳定游标分页</span>
        <nav className="dataset-pagination" aria-label="游标分页">
          <button
            className="dataset-button dataset-button--secondary"
            type="button"
            disabled={!page.pageInfo.hasPreviousPage}
            onClick={onPrevious}
          >
            上一组
          </button>
          <button
            className="dataset-button dataset-button--secondary"
            type="button"
            disabled={!page.pageInfo.hasNextPage}
            onClick={onNext}
          >
            下一组
          </button>
        </nav>
      </footer>
    </section>
  );
}
