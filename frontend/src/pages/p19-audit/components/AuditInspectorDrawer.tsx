import { Descriptions, List, Typography } from 'antd';
import type { AuditFieldVisibility } from '../../../features/audit/field-visibility';
import { resolvePendingAuditResourceLink } from '../../../features/audit/pending-links';
import type { AuditEventView } from '../../../features/audit/types';
import { EntityDrawer, PageState, StatusTag, type PageStateKind } from '../../../shared/ui';
import styles from '../styles.module.css';

function detailValue(
  value: string | number | boolean | null | readonly (string | number | boolean | null)[],
): string {
  if (value === null) return '空值';
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join('、');
  return String(value);
}

export function AuditInspectorDrawer({
  open,
  state,
  event,
  visibility,
  requestId,
  onRetry,
  onClose,
}: Readonly<{
  open: boolean;
  state: PageStateKind | 'ready';
  event?: AuditEventView;
  visibility: AuditFieldVisibility;
  requestId?: string | null;
  onRetry?: () => void;
  onClose: () => void;
}>) {
  const resourceLink = event ? resolvePendingAuditResourceLink(event.resource.type) : null;

  return (
    <EntityDrawer open={open} title="事件详情" onClose={onClose} loading={state === 'loading'} width={520}>
      {state !== 'ready' && state !== 'loading' ? (
        <PageState state={state} label="审计事件详情" requestId={requestId} onRetry={onRetry} />
      ) : event ? (
        <div className={styles.contentStack}>
          {!event.eventNameKnown || event.hasUnknownEnum ? (
            <PageState state="unknown" label="事件未知枚举" />
          ) : null}
          <Descriptions size="small" column={1}>
            <Descriptions.Item label="事件 ID"><code>{event.eventId}</code></Descriptions.Item>
            <Descriptions.Item label="事件名">{event.eventNameKnown ? event.eventName : '未登记事件'}</Descriptions.Item>
            <Descriptions.Item label="发生时间"><time dateTime={event.occurredAt}>{event.occurredAt}</time></Descriptions.Item>
            <Descriptions.Item label="记录时间"><time dateTime={event.recordedAt}>{event.recordedAt}</time></Descriptions.Item>
            <Descriptions.Item label="Actor">{event.actor.displayName}（{event.actor.type}）</Descriptions.Item>
            {visibility.actorRoleIds ? <Descriptions.Item label="角色投影">{event.actor.roles.join('、') || '无角色投影'}</Descriptions.Item> : null}
            <Descriptions.Item label="资源">
              {event.resource.displayName} / <code>{event.resource.id}</code>{' '}
              {resourceLink ? <a href={resourceLink}>打开资源</a> : '（无可用深链）'}
            </Descriptions.Item>
            <Descriptions.Item label="结果">
              <StatusTag status={event.outcome.status} known={event.outcome.status !== 'UNKNOWN'} />
            </Descriptions.Item>
            <Descriptions.Item label="风险">
              <StatusTag status={event.risk.level} known={event.risk.level !== 'UNKNOWN'} tone="warning" />
            </Descriptions.Item>
            <Descriptions.Item label="请求 ID"><code>{event.request.requestId}</code></Descriptions.Item>
            {visibility.requestMetadata ? <Descriptions.Item label="请求上下文">{event.request.clientType} / {event.request.ipAddress ?? '已脱敏'} / {event.request.deviceSummary ?? '未提供'}</Descriptions.Item> : null}
            <Descriptions.Item label="保留类别">{event.retention.className}；{event.retention.retainUntil ?? '未提供到期时间'}</Descriptions.Item>
            <Descriptions.Item label="完整性">{event.integrity.status}</Descriptions.Item>
            {visibility.integrityEvidence ? <Descriptions.Item label="完整性证据">{event.integrity.recordDigest ?? '未提供'} / {event.integrity.checkpointId ?? '未提供'}</Descriptions.Item> : null}
          </Descriptions>
          <section aria-labelledby="safe-change-title">
            <h3 id="safe-change-title">安全变更摘要</h3>
            {event.change === null ? <p>未提供或不适用</p> : (
              <>
                <Typography.Paragraph>{event.change.summaryCode}</Typography.Paragraph>
                {visibility.changeValues ? (
                  <List
                    size="small"
                    dataSource={[...event.change.changedFields]}
                    renderItem={(field) => (
                      <List.Item key={field}>
                        <div className={styles.changeField}>
                          <strong>{field}</strong>
                          <span>之前：{detailValue(event.change?.before[field] ?? null)}</span>
                          <span>之后：{detailValue(event.change?.after[field] ?? null)}</span>
                        </div>
                      </List.Item>
                    )}
                  />
                ) : <p>当前能力只允许查看变更类型。</p>}
              </>
            )}
          </section>
        </div>
      ) : null}
    </EntityDrawer>
  );
}
