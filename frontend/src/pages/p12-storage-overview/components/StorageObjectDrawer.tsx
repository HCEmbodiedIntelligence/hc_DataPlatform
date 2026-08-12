import { Alert, Button, Descriptions } from 'antd';
import type { StorageInventoryFact } from '../../../entities/storage-inventory';
import { formatByteString } from '../../../features/storage-overview/metrics-contract';
import { EntityDrawer, PageState, StatusTag, type PageStateKind } from '../../../shared/ui';

export function StorageObjectDrawer({
  open,
  state,
  object,
  requestId,
  onRetry,
  onClose,
}: Readonly<{
  open: boolean;
  state: PageStateKind | 'ready';
  object?: StorageInventoryFact;
  requestId?: string | null;
  onRetry?: () => void;
  onClose: () => void;
}>) {
  return (
    <EntityDrawer
      open={open}
      title="对象详情"
      onClose={onClose}
      loading={state === 'loading'}
      extra={(
        <Button autoFocus aria-label="关闭对象详情" onClick={onClose}>
          关闭
        </Button>
      )}
    >
      <Alert
        type="info"
        showIcon
        title="只读诊断"
        description="不提供下载、删除、Restore、Abort 或生命周期执行。"
      />
      {state !== 'ready' && state !== 'loading' ? (
        <PageState
          state={state}
          label="对象详情状态"
          requestId={requestId}
          onRetry={onRetry}
        />
      ) : object ? (
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="对象">{object.displayKey}</Descriptions.Item>
          <Descriptions.Item label="对象 ID"><code>{object.objectId}</code></Descriptions.Item>
          <Descriptions.Item label="快照"><code>{object.snapshotId}</code></Descriptions.Item>
          <Descriptions.Item label="角色">
            <StatusTag
              status={object.objectRole}
              known={object.objectRole !== 'UNKNOWN'}
              label={object.objectRole}
            />
          </Descriptions.Item>
          <Descriptions.Item label="物理量">{formatByteString(object.physicalBytes)}</Descriptions.Item>
          <Descriptions.Item label="保护原因">{object.protectionReasons.join('、') || '无'}</Descriptions.Item>
        </Descriptions>
      ) : null}
    </EntityDrawer>
  );
}
