import type { useUploadEvents } from '../../../features/ingest/api';

type EventItem = NonNullable<ReturnType<typeof useUploadEvents>['data']>['items'][number];

export function UploadEventTimeline(props: { readonly items: readonly EventItem[] }) {
  return <ol className="upload-event-timeline">{props.items.map((event) => {
    const safeFacts = Object.entries(event.safePayload).filter((entry): entry is [string, string | boolean] => entry[1] !== null && entry[1] !== undefined);
    return <li key={event.eventId}>
      <div><strong>{event.eventType}</strong> <span>{event.eventLevel}</span> <time dateTime={event.occurredAt}>{event.occurredAt}</time></div>
      <p>{event.fromState ?? '—'} → {event.toState ?? '—'}；资源版本 {event.resourceRef.version}</p>
      <p>执行者：{event.actor.displayName}（{event.actor.kind}）；请求 ID：<code>{event.requestId}</code>{event.jobId ? <>；Job：<code>{event.jobId}</code></> : null}</p>
      {safeFacts.length ? <dl>{safeFacts.map(([key, item]) => <span key={key}><dt>{key}</dt><dd>{String(item)}</dd></span>)}</dl> : null}
    </li>;
  })}</ol>;
}
