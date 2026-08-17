import { Alert, Button, Flex, Modal, Typography } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { SecureUploadPicker } from '../../../shared/ui';
import styles from '../styles.module.css';

export interface CreateUploadDraft {
  readonly dataSourceId: string;
  readonly targetDatasetId: string | null;
  readonly files: readonly File[];
}

export interface UploadCreationChoice {
  readonly id: string;
  readonly name: string;
  readonly allowed: boolean;
  readonly blockedReasons: readonly {
    readonly code: string;
    readonly message: string;
  }[];
}

export function CreateUploadDialog(props: {
  readonly open: boolean;
  readonly pending: boolean;
  readonly optionsPending: boolean;
  readonly dataSources: readonly UploadCreationChoice[];
  readonly datasets: readonly UploadCreationChoice[];
  readonly blockedReasons: readonly {
    readonly code: string;
    readonly message: string;
  }[];
  readonly initialDataSourceId?: string;
  readonly initialDatasetId?: string;
  readonly onClose: () => void;
  readonly onSubmit: (draft: CreateUploadDraft) => void;
}) {
  const [files, setFiles] = useState<readonly File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const defaultSource = useMemo(() => props.dataSources.find((source) => source.id === props.initialDataSourceId && source.allowed) ?? props.dataSources.find((source) => source.allowed), [props.dataSources, props.initialDataSourceId]);
  const targetDatasetId = useMemo(() => props.datasets.find((dataset) => dataset.id === props.initialDatasetId && dataset.allowed)?.id ?? null, [props.datasets, props.initialDatasetId]);
  const blockers = useMemo(
    () =>
      props.optionsPending || defaultSource
        ? props.blockedReasons
        : [
            ...props.blockedReasons,
            {
              code: 'NO_UPLOAD_CONFIGURATION',
              message: '当前没有可用的默认上传配置。',
            },
          ],
    [defaultSource, props.blockedReasons, props.optionsPending],
  );
  const latestSubmit = useRef(props.onSubmit);
  latestSubmit.current = props.onSubmit;

  useEffect(() => {
    if (!props.open) return;
    setFiles([]);
    setFileError(null);
    setSubmitted(false);
  }, [props.open]);

  useEffect(() => {
    if (!props.open || props.pending || props.optionsPending || blockers.length > 0 || !defaultSource || files.length === 0 || submitted) {
      return;
    }
    const timer = window.setTimeout(() => {
      setSubmitted(true);
      latestSubmit.current({
        dataSourceId: defaultSource.id,
        targetDatasetId,
        files,
      });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [blockers.length, defaultSource, files, props.open, props.optionsPending, props.pending, submitted, targetDatasetId]);

  const close = () => {
    if (props.pending) return;
    setFiles([]);
    setFileError(null);
    setSubmitted(false);
    props.onClose();
  };

  const submit = () => {
    if (!defaultSource || files.length === 0) return;
    setSubmitted(true);
    props.onSubmit({ dataSourceId: defaultSource.id, targetDatasetId, files });
  };

  return (
    <Modal open={props.open} title="上传文件或文件夹" footer={null} closable={!props.pending} keyboard={!props.pending} mask={{ closable: false }} destroyOnHidden width={720} onCancel={close}>
      <div className={styles.uploadForm}>
        <section className={styles.uploadPicker} aria-label="上传文件">
          <SecureUploadPicker
            files={files}
            onFilesChange={(nextFiles) => {
              setFiles(nextFiles);
              setSubmitted(false);
              if (nextFiles.length > 0) setFileError(null);
            }}
            onRejected={(rejection) => {
              setFileError(rejection.code === 'TOO_MANY_FILES' ? '一次最多选择 10000 个文件' : `${rejection.fileName} 超出大小限制`);
            }}
            disabled={props.pending}
            maxCount={10_000}
            multiple
            directory
            dragger
            label="选择文件"
          />
          {fileError ? (
            <Typography.Text type="danger" role="alert">
              {fileError}
            </Typography.Text>
          ) : null}
        </section>
        {blockers.length > 0 ? (
          <Alert
            type="error"
            showIcon
            title="当前不能上传"
            description={
              <ul>
                {blockers.map((reason) => (
                  <li key={reason.code}>{reason.message}</li>
                ))}
              </ul>
            }
          />
        ) : null}
        {files.length > 0 ? <Typography.Text type="secondary">{props.pending ? '正在开始上传…' : submitted ? '上传未完成时可重试。' : `已选择 ${files.length} 个文件，即将自动上传…`}</Typography.Text> : null}
        <Flex justify="end" gap="small" wrap="wrap">
          <Button disabled={props.pending} onClick={close}>
            取消
          </Button>
          {submitted && !props.pending ? (
            <Button type="primary" disabled={props.optionsPending || blockers.length > 0} onClick={submit}>
              重试上传
            </Button>
          ) : null}
        </Flex>
      </div>
    </Modal>
  );
}
