import { useEffect, useRef } from 'react';
import { formText } from '../../../features/ingest/form-data';

export interface CreateUploadDraft {
  readonly dataSourceId: string;
  readonly targetDatasetId: string | null;
  readonly files: readonly File[];
}

export interface UploadCreationChoice {
  readonly id: string;
  readonly name: string;
  readonly allowed: boolean;
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
}

export function CreateUploadDialog(props: {
  readonly open: boolean;
  readonly pending: boolean;
  readonly optionsPending: boolean;
  readonly dataSources: readonly UploadCreationChoice[];
  readonly datasets: readonly UploadCreationChoice[];
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
  readonly initialDataSourceId?: string;
  readonly initialDatasetId?: string;
  readonly onClose: () => void;
  readonly onSubmit: (draft: CreateUploadDraft) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (props.open && !ref.current?.open) ref.current?.showModal();
    if (!props.open && ref.current?.open) ref.current.close();
  }, [props.open]);
  return <dialog ref={ref} onClose={props.onClose} aria-labelledby="create-upload-title"><form onSubmit={(event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const files = data.getAll('files').filter((item): item is File => item instanceof File && item.size > 0);
    props.onSubmit({ dataSourceId: formText(data, 'dataSourceId'), targetDatasetId: formText(data, 'targetDatasetId') || null, files });
  }}><h2 id="create-upload-title">新建上传</h2><label>数据源<select name="dataSourceId" required defaultValue={props.initialDataSourceId ?? ''}><option value="" disabled>选择数据源</option>{props.dataSources.map((source) => <option key={source.id} value={source.id} disabled={!source.allowed}>{source.name} · {source.id}{source.allowed ? '' : '（不可用）'}</option>)}</select></label><label>目标 Dataset<select name="targetDatasetId" required defaultValue={props.initialDatasetId ?? ''}><option value="" disabled>选择 Dataset</option>{props.datasets.map((dataset) => <option key={dataset.id} value={dataset.id} disabled={!dataset.allowed}>{dataset.name} · {dataset.id}{dataset.allowed ? '' : '（不可用）'}</option>)}</select></label><label>文件<input name="files" type="file" multiple required /></label>{props.blockedReasons.length ? <ul>{props.blockedReasons.map((reason) => <li key={reason.code}>{reason.code}：{reason.message}</li>)}</ul> : null}<p>浏览器将通过短时 no-store 授权直传 OSS Multipart；文件内容不会经过业务 API。对象 SHA-256 在 Manifest 提交前补齐，与 Multipart ETag 不等价。</p><div className="dialog-actions"><button type="button" onClick={props.onClose}>取消</button><button type="submit" disabled={props.pending || props.optionsPending || props.blockedReasons.length > 0}>{props.optionsPending ? '正在加载选项…' : props.pending ? '正在创建会话…' : '创建并上传'}</button></div></form></dialog>;
}
