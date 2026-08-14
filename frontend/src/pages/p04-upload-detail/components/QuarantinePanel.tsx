import { Alert, Button, Card, Descriptions, Space, Typography } from 'antd';
import type { QuarantineSummary } from '../../../features/ingest/upload/model';
import { StatusTag } from '../../../shared/ui';

export function QuarantinePanel(props: {
  readonly quarantine: QuarantineSummary | null;
  readonly onRequestRelease: () => void;
  readonly canRequestRelease: boolean;
}) {
  if (!props.quarantine) return <Alert type="info" showIcon title="当前会话没有隔离事实。" />;
  const disposition = typeof props.quarantine.disposition === 'string'
    ? props.quarantine.disposition
    : `UNKNOWN (${props.quarantine.disposition.raw})`;
  return (
    <Card size="small" title="隔离事实">
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Descriptions bordered column={{ xs: 1, sm: 2 }} size="small">
          <Descriptions.Item label="隔离 ID"><Typography.Text code>{props.quarantine.quarantineId}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="处置状态">
            <StatusTag status={disposition} label={disposition} known={typeof props.quarantine.disposition === 'string'} tone="warning" />
            {typeof props.quarantine.disposition === 'string' ? null : <Typography.Text>{disposition}</Typography.Text>}
          </Descriptions.Item>
          <Descriptions.Item label="原因">{props.quarantine.reasonCode}：{props.quarantine.safeSummary}</Descriptions.Item>
          <Descriptions.Item label="保留至"><time dateTime={props.quarantine.retainUntil}>{props.quarantine.retainUntil}</time></Descriptions.Item>
        </Descriptions>
        <Alert type="warning" showIcon title="隔离对象不会获得普通内容 URL。释放只能通过同一对象集复验和原子可用性提交完成。" />
        <Button danger type="primary" disabled={!props.canRequestRelease} onClick={props.onRequestRelease}>复验并申请释放</Button>
      </Space>
    </Card>
  );
}
