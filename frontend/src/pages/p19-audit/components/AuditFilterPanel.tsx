import { Input, Select } from 'antd';
import {
  canonicalAuditEventNames,
  type CanonicalAuditEventName,
} from '../../../features/audit/event-catalog';
import type { AuditSearch } from '../../../features/audit/routing';
import styles from '../styles.module.css';

export function AuditFilterPanel({
  search,
  onChange,
}: Readonly<{
  search: AuditSearch;
  onChange: (patch: Partial<AuditSearch>) => void;
}>) {
  return (
    <form
      className={styles.filterForm}
      aria-label="审计日志筛选"
      onSubmit={(event) => event.preventDefault()}
    >
      <label className={styles.filterField}>
        <span>开始（含）</span>
        <Input
          type="datetime-local"
          value={search.from.slice(0, 16)}
          onChange={(event) => {
            const value = new Date(event.target.value);
            if (Number.isFinite(value.getTime())) onChange({ from: value.toISOString() });
          }}
        />
      </label>
      <label className={styles.filterField}>
        <span>结束（不含）</span>
        <Input
          type="datetime-local"
          value={search.to.slice(0, 16)}
          onChange={(event) => {
            const value = new Date(event.target.value);
            if (Number.isFinite(value.getTime())) onChange({ to: value.toISOString() });
          }}
        />
      </label>
      <label className={styles.filterField}>
        <span>事件名</span>
        <Select
          value={search.eventName[0] ?? ''}
          options={[
            { label: '全部事件', value: '' },
            ...canonicalAuditEventNames.map((name) => ({ label: name, value: name })),
          ]}
          onChange={(eventName) => onChange({
            eventName: eventName ? [eventName as CanonicalAuditEventName] : [],
          })}
        />
      </label>
      <label className={styles.filterField}>
        <span>Actor</span>
        <Input
          value={search.actorId[0] ?? ''}
          onChange={(event) => onChange({ actorId: event.target.value ? [event.target.value] : [] })}
        />
      </label>
      <label className={styles.filterField}>
        <span>目标类型</span>
        <Input
          value={search.resourceType[0] ?? ''}
          onChange={(event) => onChange({
            resourceType: event.target.value ? [event.target.value.toUpperCase()] : [],
          })}
        />
      </label>
      <label className={styles.filterField}>
        <span>目标 ID</span>
        <Input
          value={search.resourceId ?? ''}
          onChange={(event) => onChange(
            event.target.value ? { resourceId: event.target.value } : { resourceId: undefined },
          )}
        />
      </label>
      <label className={styles.filterField}>
        <span>结果</span>
        <Select
          value={search.result[0] ?? ''}
          options={['', 'SUCCEEDED', 'DENIED', 'FAILED', 'PARTIAL'].map((value) => ({
            label: value || '全部结果',
            value,
          }))}
          onChange={(result) => onChange({
            result: result ? [result as AuditSearch['result'][number]] : [],
          })}
        />
      </label>
      <label className={styles.filterField}>
        <span>风险</span>
        <Select
          value={search.riskLevel[0] ?? ''}
          options={['', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((value) => ({
            label: value || '全部风险',
            value,
          }))}
          onChange={(riskLevel) => onChange({
            riskLevel: riskLevel ? [riskLevel as AuditSearch['riskLevel'][number]] : [],
          })}
        />
      </label>
      <label className={styles.filterField}>
        <span>请求 ID</span>
        <Input
          value={search.requestId ?? ''}
          onChange={(event) => onChange(
            event.target.value ? { requestId: event.target.value } : { requestId: undefined },
          )}
        />
      </label>
    </form>
  );
}
