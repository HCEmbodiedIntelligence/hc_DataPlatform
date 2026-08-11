import type { DataSourceSummary } from '../../../entities/data-source';
import { credentialDisplay } from '../../../features/ingest/connectors/credential-boundary';

function label(value: string | { readonly kind: 'UNKNOWN'; readonly raw: string }): string {
  return typeof value === 'string' ? value : `未知（${value.raw}）`;
}

export function DataSourceTable(props: {
  readonly items: readonly DataSourceSummary[];
  readonly selectedId?: string;
  readonly onSelect: (id: string) => void;
}) {
  return (
    <div className="ingest-table-scroll">
      <table>
        <caption>数据源连接器列表</caption>
        <thead><tr><th scope="col">数据源</th><th scope="col">连接器</th><th scope="col">连接状态</th><th scope="col">凭据</th><th scope="col">最近上传</th><th scope="col">操作</th></tr></thead>
        <tbody>
          {props.items.map((source) => (
            <tr key={source.id} data-selected={source.id === props.selectedId}>
              <th scope="row"><span>{source.name}</span><code>{source.id}</code></th>
              <td>{label(source.sourceType)}</td>
              <td><span className={`status status-${label(source.connectivity.state).toLowerCase()}`}>{label(source.connectivity.state)}</span></td>
              <td>{credentialDisplay(source.credential.maskedHint, source.credential.configured)}</td>
              <td>{source.lastUpload ? <time dateTime={source.lastUpload.completedAt}>{source.lastUpload.completedAt}</time> : '暂无上传'}</td>
              <td><button type="button" onClick={() => props.onSelect(source.id)} aria-label={`查看 ${source.name}`}>查看</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
