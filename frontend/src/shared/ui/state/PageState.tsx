import { Alert, Button, Empty, Result, Skeleton, Space } from 'antd';
import {
  Ban,
  CircleAlert,
  CircleHelp,
  FileQuestion,
  Gauge,
  PlugZap,
  RefreshCw,
  ShieldAlert,
  WifiOff,
} from 'lucide-react';
import type { ReactNode } from 'react';
import type { PageStateKind } from './contracts';
import styles from './state.module.css';

interface StateCopy {
  title: string;
  description: string;
}

const stateCopy: Readonly<Record<Exclude<PageStateKind, 'loading' | 'refreshing'>, StateCopy>> = {
  empty: { title: '暂无数据', description: '当前范围内还没有可显示的数据。' },
  'filtered-empty': { title: '当前筛选没有结果', description: '调整或清除筛选条件后重试。' },
  error: { title: '此区域暂时不可用', description: '加载失败，其他可用区域不受影响。' },
  forbidden: { title: '无权访问', description: '当前授权不允许读取此资源。' },
  'not-found': { title: '资源不存在', description: '资源 ID 无效，或资源不属于当前作用域。' },
  gone: { title: '资源已失效', description: '资源已归档、过期或不再可用。' },
  conflict: { title: '服务器事实已变化', description: '请刷新数据并重新确认本次操作。' },
  'rate-limited': { title: '请求频率受限', description: '请按服务端提示等待后再手动重试。' },
  offline: {
    title: '网络连接中断',
    description: '当前数据可能已过期；连接恢复前写操作应保持禁用。',
  },
  'contract-mismatch': {
    title: '数据合同不匹配',
    description: '响应未通过合同校验。为避免写错资源，此区域已安全关闭。',
  },
  unknown: {
    title: '发现未知状态',
    description: '当前客户端无法安全解释此状态，资源仅以只读方式展示。',
  },
  'feature-unavailable': {
    title: '能力尚未开放',
    description: '当前环境未提供此能力的完整合同，不展示半成品写入口。',
  },
};

const icons: Readonly<
  Record<Exclude<PageStateKind, 'loading' | 'refreshing' | 'empty' | 'filtered-empty'>, ReactNode>
> = {
  error: <CircleAlert aria-hidden="true" />,
  forbidden: <Ban aria-hidden="true" />,
  'not-found': <FileQuestion aria-hidden="true" />,
  gone: <FileQuestion aria-hidden="true" />,
  conflict: <RefreshCw aria-hidden="true" />,
  'rate-limited': <Gauge aria-hidden="true" />,
  offline: <WifiOff aria-hidden="true" />,
  'contract-mismatch': <ShieldAlert aria-hidden="true" />,
  unknown: <CircleHelp aria-hidden="true" />,
  'feature-unavailable': <PlugZap aria-hidden="true" />,
};

export interface PageStateProps {
  state: PageStateKind;
  label?: string;
  title?: string;
  description?: ReactNode;
  requestId?: string | null;
  onRetry?: () => void;
  retryLabel?: string;
  action?: ReactNode;
  children?: ReactNode;
}

export function PageState({
  state,
  label = '页面区域',
  title,
  description,
  requestId,
  onRetry,
  retryLabel = '重试',
  action,
  children,
}: Readonly<PageStateProps>) {
  if (state === 'loading') {
    return (
      <section
        className={styles.pageState}
        data-page-state={state}
        aria-label={`${label}加载中`}
        aria-busy="true"
      >
        <Skeleton active title paragraph={{ rows: 6 }} />
        <span className={styles.srOnly} role="status">
          {label}加载中
        </span>
      </section>
    );
  }

  if (state === 'refreshing') {
    return (
      <section className={styles.refreshingState} data-page-state={state} aria-busy="true">
        <Alert
          type="info"
          showIcon
          title={title ?? '正在刷新'}
          description={description ?? '当前显示上次成功数据。'}
        />
        <div aria-live="polite" className={styles.srOnly}>
          {label}正在刷新
        </div>
        {children}
      </section>
    );
  }

  const copy = stateCopy[state];
  const resolvedTitle = title ?? copy.title;
  const resolvedDescription = description ?? copy.description;
  const readOnly =
    state === 'unknown' || state === 'contract-mismatch' || state === 'feature-unavailable';
  const extra = (
    <Space orientation="vertical" align="center" size="small">
      {requestId ? (
        <span>
          请求 ID：<code>{requestId}</code>
        </span>
      ) : null}
      <Space wrap>
        {onRetry ? <Button onClick={onRetry}>{retryLabel}</Button> : null}
        {!readOnly ? action : null}
      </Space>
    </Space>
  );

  if (state === 'empty' || state === 'filtered-empty') {
    return (
      <section className={styles.pageState} data-page-state={state} aria-label={label}>
        <Empty
          description={
            <>
              <strong>{resolvedTitle}</strong>
              <span className={styles.stateDescription}>{resolvedDescription}</span>
            </>
          }
        >
          {extra}
        </Empty>
      </section>
    );
  }

  const status =
    state === 'not-found'
      ? '404'
      : state === 'forbidden'
        ? '403'
        : state === 'error'
          ? '500'
          : 'warning';
  const liveRole = state === 'unknown' ? 'status' : 'alert';

  return (
    <section
      className={styles.pageState}
      data-page-state={state}
      data-safe-mode={readOnly ? 'read-only' : undefined}
      aria-label={label}
      role={liveRole}
    >
      <Result
        status={status}
        icon={icons[state]}
        title={resolvedTitle}
        subTitle={resolvedDescription}
        extra={extra}
      />
    </section>
  );
}
