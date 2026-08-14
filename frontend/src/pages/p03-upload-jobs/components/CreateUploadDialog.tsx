import { Alert, Button, Flex, Modal, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import {
  createZodResolver,
  RHFSelect,
  SecureUploadPicker,
} from '../../../shared/ui';
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
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
}

const uploadSchema = z.object({
  dataSourceId: z.string().trim().min(1, '请选择数据源'),
  targetDatasetId: z.string().trim().min(1, '请选择目标 Dataset'),
});

type UploadFormValues = z.infer<typeof uploadSchema>;

function defaults(initialDataSourceId?: string, initialDatasetId?: string): UploadFormValues {
  return {
    dataSourceId: initialDataSourceId ?? '',
    targetDatasetId: initialDatasetId ?? '',
  };
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
  const form = useForm<UploadFormValues>({
    defaultValues: defaults(props.initialDataSourceId, props.initialDatasetId),
    mode: 'onChange',
    resolver: createZodResolver(uploadSchema),
  });
  const [files, setFiles] = useState<readonly File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    if (!props.open) return;
    form.reset(defaults(props.initialDataSourceId, props.initialDatasetId));
    setFiles([]);
    setFileError(null);
  }, [form, props.initialDataSourceId, props.initialDatasetId, props.open]);

  const close = () => {
    if (props.pending) return;
    form.reset(defaults(props.initialDataSourceId, props.initialDatasetId));
    setFiles([]);
    setFileError(null);
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title="新建上传"
      footer={null}
      closable={!props.pending}
      keyboard={!props.pending}
      mask={{ closable: false }}
      destroyOnHidden
      width={720}
      onCancel={close}
      afterOpenChange={(open) => {
        if (open) form.setFocus('dataSourceId');
      }}
    >
      <form
        className={styles.uploadForm}
        onSubmit={(event) => {
          void form.handleSubmit((value) => {
            if (files.length === 0) {
              setFileError('请至少选择一个文件');
              return;
            }
            setFileError(null);
            props.onSubmit({
              dataSourceId: value.dataSourceId,
              targetDatasetId: value.targetDatasetId || null,
              files,
            });
          })(event);
        }}
      >
        <RHFSelect
          control={form.control}
          name="dataSourceId"
          label="数据源"
          placeholder="选择数据源"
          disabled={props.pending || props.optionsPending}
          options={props.dataSources.map((source) => ({
            value: source.id,
            label: `${source.name} · ${source.id}${source.allowed ? '' : '（不可用）'}`,
            disabled: !source.allowed,
          }))}
        />
        <RHFSelect
          control={form.control}
          name="targetDatasetId"
          label="目标 Dataset"
          placeholder="选择 Dataset"
          disabled={props.pending || props.optionsPending}
          options={props.datasets.map((dataset) => ({
            value: dataset.id,
            label: `${dataset.name} · ${dataset.id}${dataset.allowed ? '' : '（不可用）'}`,
            disabled: !dataset.allowed,
          }))}
        />
        <section className={styles.uploadPicker} aria-label="上传文件">
          <Typography.Text strong>文件</Typography.Text>
          <SecureUploadPicker
            files={files}
            onFilesChange={(nextFiles) => {
              setFiles(nextFiles);
              if (nextFiles.length > 0) setFileError(null);
            }}
            onRejected={(rejection) => {
              setFileError(rejection.code === 'TOO_MANY_FILES' ? '一次最多选择 100 个文件' : `${rejection.fileName} 超出大小限制`);
            }}
            disabled={props.pending}
            maxCount={100}
            multiple
            label="选择待上传文件"
          />
          {fileError ? <Typography.Text type="danger" role="alert">{fileError}</Typography.Text> : null}
        </section>
        {props.blockedReasons.length > 0 ? (
          <Alert
            type="error"
            showIcon
            title="当前条件不允许创建上传"
            description={<ul>{props.blockedReasons.map((reason) => <li key={reason.code}>{reason.code}：{reason.message}</li>)}</ul>}
          />
        ) : null}
        <Alert
          type="info"
          showIcon
          title="受控直传"
          description="浏览器将通过短时 no-store 授权直传 OSS Multipart；文件内容不会经过业务 API。对象 SHA-256 会在 Manifest 提交前补齐，且不等同于 Multipart ETag。"
        />
        <Flex justify="end" gap="small" wrap="wrap">
          <Button disabled={props.pending} onClick={close}>取消</Button>
          <Button
            type="primary"
            htmlType="submit"
            loading={props.pending}
            disabled={props.optionsPending || props.blockedReasons.length > 0}
          >
            {props.optionsPending ? '正在加载选项…' : props.pending ? '正在创建会话…' : '创建并上传'}
          </Button>
        </Flex>
      </form>
    </Modal>
  );
}
