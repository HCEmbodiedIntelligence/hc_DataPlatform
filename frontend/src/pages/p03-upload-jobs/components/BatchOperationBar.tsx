import { Alert, Button, Flex, Space } from 'antd';

export function BatchOperationBar(props: {
  readonly count: number;
  readonly pauseDisabled: boolean;
  readonly cancelDisabled: boolean;
  readonly pending: boolean;
  readonly result?: { readonly succeeded: number; readonly failed: number };
  readonly onPause: () => void;
  readonly onCancel: () => void;
  readonly onClear: () => void;
}) {
  if (!props.count) return null;
  return (
    <Alert
      type={props.result?.failed ? 'warning' : 'info'}
      showIcon
      title={`已选择 ${props.count} 项`}
      description={(
        <Flex gap="small" wrap="wrap" align="center" role="region" aria-label="批量操作">
          <Button disabled={props.pauseDisabled || props.pending} onClick={props.onPause}>批量暂停</Button>
          <Button danger disabled={props.cancelDisabled || props.pending} onClick={props.onCancel}>批量取消</Button>
          <Button disabled={props.pending} onClick={props.onClear}>清除选择</Button>
          {props.pending ? (
            <span role="status">正在逐项提交（每项独立 ETag）…</span>
          ) : props.result ? (
            <Space role="status">成功 {props.result.succeeded}，失败 {props.result.failed}</Space>
          ) : null}
        </Flex>
      )}
    />
  );
}
