import { Button, Upload, Typography } from 'antd';
import type { UploadFile, UploadProps } from 'antd';
import { Paperclip, Trash2, Upload as UploadIcon } from 'lucide-react';
import { useEffect, useRef } from 'react';

export interface SecureUploadRejection {
  readonly code: 'FILE_TOO_LARGE' | 'TOO_MANY_FILES';
  readonly fileName: string;
}

export interface SecureUploadPickerProps {
  readonly files: readonly File[];
  readonly onFilesChange: (files: readonly File[]) => void;
  readonly onRejected?: (rejection: SecureUploadRejection) => void;
  readonly accept?: UploadProps['accept'];
  readonly disabled?: boolean;
  readonly maxCount?: number;
  readonly maxSizeBytes?: number;
  readonly multiple?: boolean;
  readonly directory?: boolean;
  readonly dragger?: boolean;
  readonly label?: string;
}

function useStableFileIds() {
  const ids = useRef(new WeakMap<File, string>());
  const sequence = useRef(0);
  return (file: File): string => {
    const current = ids.current.get(file);
    if (current) return current;
    sequence.current += 1;
    const next = `local-file-${sequence.current}`;
    ids.current.set(file, next);
    return next;
  };
}

export function SecureUploadPicker({ accept, disabled = false, files, label = '选择本地文件', maxCount = 1, maxSizeBytes, multiple = false, directory = false, dragger = false, onFilesChange, onRejected }: SecureUploadPickerProps) {
  const fileId = useStableFileIds();
  const changeRef = useRef(onFilesChange);
  const hasFilesRef = useRef(files.length > 0);
  changeRef.current = onFilesChange;
  hasFilesRef.current = files.length > 0;

  useEffect(
    () => () => {
      if (hasFilesRef.current) changeRef.current([]);
    },
    [],
  );

  const fileList: UploadFile[] = files.map((file) => ({
    uid: fileId(file),
    name: file.name,
    size: file.size,
    type: file.type,
    originFileObj: file as UploadFile['originFileObj'],
  }));

  const appendFiles = (batch: readonly File[]) => {
    const accepted = batch.filter((file) => {
      if (maxSizeBytes === undefined || file.size <= maxSizeBytes) return true;
      onRejected?.({ code: 'FILE_TOO_LARGE', fileName: file.name });
      return false;
    });
    if (files.length + accepted.length > maxCount) {
      onRejected?.({
        code: 'TOO_MANY_FILES',
        fileName: accepted.at(-1)?.name ?? '',
      });
      return;
    }
    onFilesChange([...files, ...accepted]);
  };

  const uploadProps: UploadProps = {
    accept,
    disabled,
    directory,
    fileList,
    maxCount,
    multiple: multiple || directory,
    beforeUpload: (file, batch) => {
      if (maxSizeBytes !== undefined && file.size > maxSizeBytes) {
        onRejected?.({ code: 'FILE_TOO_LARGE', fileName: file.name });
        return Upload.LIST_IGNORE;
      }
      if (files.length + batch.length > maxCount) {
        onRejected?.({ code: 'TOO_MANY_FILES', fileName: file.name });
        return Upload.LIST_IGNORE;
      }
      return false;
    },
    onChange: ({ fileList: nextFileList }) => {
      const nextFiles = nextFileList
        .map((item) => item.originFileObj)
        .filter((file): file is NonNullable<typeof file> => file !== undefined)
        .slice(-maxCount);
      onFilesChange(nextFiles);
    },
    onRemove: (removed) => {
      onFilesChange(files.filter((file) => fileId(file) !== removed.uid));
      return false;
    },
    showUploadList: {
      showDownloadIcon: false,
      showPreviewIcon: false,
      removeIcon: <Trash2 aria-label="移除文件" size={16} />,
    },
  };

  return (
    <div data-component="SecureUploadPicker">
      {dragger ? (
        <>
          <Upload.Dragger {...uploadProps}>
            <UploadIcon aria-hidden="true" size={30} />
            <Typography.Paragraph strong>拖放文件或文件夹到此处</Typography.Paragraph>
            <Typography.Text type="secondary">也可点击选择文件夹</Typography.Text>
          </Upload.Dragger>
          {directory ? (
            <Upload
              accept={accept}
              disabled={disabled}
              fileList={[]}
              multiple
              beforeUpload={(file, batch) => {
                if (file.uid === batch.at(-1)?.uid) appendFiles(batch);
                return Upload.LIST_IGNORE;
              }}
              showUploadList={false}
            >
              <Button disabled={disabled} icon={<UploadIcon aria-hidden="true" size={16} />}>
                选择文件
              </Button>
            </Upload>
          ) : null}
        </>
      ) : (
        <Upload {...uploadProps}>
          <Button disabled={disabled} icon={<UploadIcon aria-hidden="true" size={16} />}>
            {label}
          </Button>
        </Upload>
      )}
      <Typography.Text type="secondary">
        <Paperclip aria-hidden="true" size={14} /> {directory ? '会保留文件夹内的相对路径；' : ''}传输由受控上传流程处理。
      </Typography.Text>
    </div>
  );
}
