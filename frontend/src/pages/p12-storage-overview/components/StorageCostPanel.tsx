import { Alert, Card, Descriptions } from 'antd';
import { displayMinorUnitMetric } from '../../../features/storage-overview/metrics-contract';
import type { StorageCostBreakdown } from '../../../features/storage-overview/types';

export function StorageCostPanel({ cost }: Readonly<{ cost: StorageCostBreakdown }>) {
  return (
    <Card title={<h2>{cost.billingPeriod} 费用构成</h2>} size="small">
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
        <Descriptions.Item label="存储">{displayMinorUnitMetric(cost.storage, cost.currency)}</Descriptions.Item>
        <Descriptions.Item label="请求">{displayMinorUnitMetric(cost.request, cost.currency)}</Descriptions.Item>
        <Descriptions.Item label="传输">{displayMinorUnitMetric(cost.transfer, cost.currency)}</Descriptions.Item>
        <Descriptions.Item label="税费">{displayMinorUnitMetric(cost.tax, cost.currency)}</Descriptions.Item>
        <Descriptions.Item label="合计">{displayMinorUnitMetric(cost.total, cost.currency)}</Descriptions.Item>
      </Descriptions>
      <Alert
        type="info"
        showIcon
        title="费用口径"
        description="以上金额来自服务端账单，页面不会根据容量自行估算费用。"
      />
    </Card>
  );
}
