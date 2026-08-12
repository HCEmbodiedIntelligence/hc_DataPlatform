import { List, Typography } from 'antd';
import type { DashboardPendingItem } from '../../../features/dashboard/types';
import { StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

export function DashboardPendingList({ items }: Readonly<{ items: readonly DashboardPendingItem[] }>) {
  return (
    <List
      size="small"
      dataSource={[...items]}
      renderItem={(item) => (
        <List.Item key={item.itemId}>
          <div className={styles.pendingItem}>
            <div className={styles.pendingTitle}>
              <Typography.Text strong>{item.title}</Typography.Text>
              <StatusTag
                status={item.status}
                label={item.status}
                known={!item.hasUnknownEnum}
                tone="warning"
              />
            </div>
            {item.summary ? <Typography.Text type="secondary">{item.summary}</Typography.Text> : null}
            <time dateTime={item.updatedAt}>{item.updatedAt}</time>
            {item.clickable ? (
              <Typography.Text type="secondary" title="目标页 builder 待 Owner 交付">
                目标暂不可用
              </Typography.Text>
            ) : null}
          </div>
        </List.Item>
      )}
    />
  );
}
