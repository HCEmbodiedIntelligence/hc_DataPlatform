import { Descriptions, Space, Tag, Timeline, Typography } from 'antd';
import type { useUploadEvents } from '../../../features/ingest/api';

type EventItem = NonNullable<ReturnType<typeof useUploadEvents>['data']>['items'][number];

export function UploadEventTimeline(props: { readonly items: readonly EventItem[] }) {
  return (
    <Timeline
      items={props.items.map((event) => {
        const safeFacts = Object.entries(event.safePayload).filter(
          (entry): entry is [string, string | boolean] => entry[1] !== null && entry[1] !== undefined,
        );
        return {
          key: event.eventId,
          content: (
            <Space orientation="vertical" size="small">
              <Space wrap>
                <Typography.Text strong>{event.eventType}</Typography.Text>
                <Tag>{event.eventLevel}</Tag>
                <time dateTime={event.occurredAt}>{event.occurredAt}</time>
              </Space>
              <Typography.Text>{event.fromState ?? '—'} → {event.toState ?? '—'}；资源版本 {event.resourceRef.version}</Typography.Text>
              <Typography.Text>
                执行者：{event.actor.displayName}（{event.actor.kind}）；请求 ID：<Typography.Text code>{event.requestId}</Typography.Text>
                {event.jobId ? <>；Job：<Typography.Text code>{event.jobId}</Typography.Text></> : null}
              </Typography.Text>
              {safeFacts.length ? (
                <Descriptions bordered column={{ xs: 1, sm: 2 }} size="small">
                  {safeFacts.map(([key, item]) => <Descriptions.Item key={key} label={key}>{String(item)}</Descriptions.Item>)}
                </Descriptions>
              ) : null}
            </Space>
          ),
        };
      })}
    />
  );
}
