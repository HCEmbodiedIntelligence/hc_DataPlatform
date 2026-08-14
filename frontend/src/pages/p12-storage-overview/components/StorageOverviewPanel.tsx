import { Card, Descriptions, Typography } from 'antd';
import { displayByteMetric } from '../../../features/storage-overview/metrics-contract';
import type { StorageOverview } from '../../../features/storage-overview/types';
import { StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';
import { StorageVisualCharts } from './StorageVisualCharts';

export function StorageOverviewPanel({ overview }: Readonly<{ overview: StorageOverview }>) {
  const reconciliationKnown = overview.reconciliation.status !== 'UNKNOWN';

  return (
    <div className={`${styles.contentStack} ${styles.overviewPanel}`}>
      <StorageVisualCharts overview={overview} />
      <Card title={<h2>Inventory 对账</h2>} size="small">
        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
          <Descriptions.Item label="状态">
            <StatusTag
              status={overview.reconciliation.status}
              known={reconciliationKnown}
              tone={overview.reconciliation.status === 'MATCHED' ? 'success' : 'warning'}
            />
          </Descriptions.Item>
          <Descriptions.Item label="已登记物理量">
            {displayByteMetric(overview.reconciliation.registeredPhysicalBytes)}
          </Descriptions.Item>
          <Descriptions.Item label="实际 OSS 物理量">
            {displayByteMetric(overview.reconciliation.actualOssPhysicalBytes)}
          </Descriptions.Item>
          <Descriptions.Item label="未分类">
            {displayByteMetric(overview.reconciliation.unclassifiedBytes)}
          </Descriptions.Item>
          <Descriptions.Item label="Multipart">
            {displayByteMetric(overview.reconciliation.multipartBytes)}
          </Descriptions.Item>
        </Descriptions>
        {overview.reconciliation.note ? (
          <Typography.Paragraph type="secondary" className={styles.panelNote}>
            {overview.reconciliation.note}
          </Typography.Paragraph>
        ) : null}
      </Card>
    </div>
  );
}
