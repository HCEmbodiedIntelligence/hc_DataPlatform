import { Alert, Button, Descriptions, Input, Modal, Space, Typography } from 'antd';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

const hiddenSensitiveDetail = '敏感详情已隐藏，请在受控详情页查看。';
const sensitiveDisplayValue =
  /(?:bearer\s+[a-z0-9._~+/-]+=*|eyj[a-z0-9_-]*\.[a-z0-9_-]+\.[a-z0-9_-]+|(?:akia|asia)[a-z0-9]{16}|(?:token|secret|signature|credential|password|access[_-]?key)\s*[:=]|(?:密钥|口令|密码|访问令牌)\s*[:：=]|[?&](?:x-amz-[^=&#\s]*|signature|token|expires)=[^&#\s]+|(?:https?|s3|oss):\/\/\S+)/iu;

function safeDisplayText(value: string): string {
  return sensitiveDisplayValue.test(value) ? hiddenSensitiveDetail : value;
}

export interface DangerBlocker {
  readonly code: string;
  readonly message: string;
}

export interface DangerPreflightEvidence {
  readonly preparedAt: string;
  readonly expiresAt: string;
  readonly resourceVersion: string;
  readonly scopeKey: string;
}

export interface DangerConflict {
  readonly status: 409 | 412;
  readonly code: string;
}

export interface DangerConfirmationInput {
  readonly expectedText: string;
  readonly label?: string;
}

export interface DangerConfirmModalProps {
  readonly open: boolean;
  readonly title: string;
  readonly actionLabel: string;
  readonly resourceId: string;
  readonly impact: string | readonly string[];
  readonly blockers?: readonly DangerBlocker[];
  readonly preflight: DangerPreflightEvidence | null;
  readonly currentScopeKey: string;
  readonly confirmation?: DangerConfirmationInput;
  readonly pending?: boolean;
  readonly conflict?: DangerConflict | null;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
  readonly onResolveConflict?: (status: DangerConflict['status']) => void;
}

function parseTime(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function DangerConfirmModal({
  actionLabel,
  blockers = [],
  confirmation,
  conflict = null,
  currentScopeKey,
  impact,
  onCancel,
  onConfirm,
  onResolveConflict,
  open,
  pending = false,
  preflight,
  resourceId,
  title,
}: DangerConfirmModalProps) {
  const [confirmationText, setConfirmationText] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const cancelButton = useRef<HTMLButtonElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (open && !wasOpen.current) {
      previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setConfirmationText('');
      setNow(Date.now());
    } else if (!open && wasOpen.current) {
      previousFocus.current?.focus();
    }
    wasOpen.current = open;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const expiresAt = parseTime(preflight?.expiresAt);
    if (expiresAt === null || expiresAt <= Date.now()) {
      setNow(Date.now());
      return undefined;
    }
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(expiresAt - Date.now() + 1, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [open, preflight?.expiresAt]);

  useEffect(
    () => () => {
      if (wasOpen.current) previousFocus.current?.focus();
    },
    [],
  );

  const preparedAt = parseTime(preflight?.preparedAt);
  const expiresAt = parseTime(preflight?.expiresAt);
  const impacts = typeof impact === 'string' ? [impact] : impact;
  const impactFingerprint = impacts.join('\u001f');
  const blockerFingerprint = blockers.map(({ code, message }) => `${code}\u001e${message}`).join('\u001f');
  const missingPreflight =
    preflight === null ||
    preparedAt === null ||
    expiresAt === null ||
    preflight.resourceVersion.trim().length === 0 ||
    preflight.scopeKey.trim().length === 0;
  const invalidChronology = preparedAt !== null && expiresAt !== null && preparedAt >= expiresAt;
  const notYetPrepared = preparedAt !== null && preparedAt > now;
  const expired = expiresAt !== null && expiresAt <= now;
  const scopeChanged = preflight !== null && preflight.scopeKey !== currentScopeKey;
  const missingOperationIdentity =
    title.trim().length === 0 || actionLabel.trim().length === 0 || resourceId.trim().length === 0;
  const missingImpact = impacts.length === 0 || impacts.every((item) => item.trim().length === 0);
  const invalidConfirmation = confirmation !== undefined && confirmation.expectedText.length === 0;
  const confirmationMatches = confirmation === undefined || confirmationText === confirmation.expectedText;
  const canConfirm =
    !pending &&
    !missingOperationIdentity &&
    !missingImpact &&
    !missingPreflight &&
    !invalidChronology &&
    !notYetPrepared &&
    !expired &&
    !scopeChanged &&
    blockers.length === 0 &&
    conflict === null &&
    !invalidConfirmation &&
    confirmationMatches;

  useEffect(() => {
    if (open) setConfirmationText('');
  }, [
    actionLabel,
    blockerFingerprint,
    confirmation?.expectedText,
    currentScopeKey,
    impactFingerprint,
    open,
    preflight?.expiresAt,
    preflight?.preparedAt,
    preflight?.resourceVersion,
    preflight?.scopeKey,
    resourceId,
    title,
  ]);

  const failClosedReasons = [
    missingOperationIdentity ? '缺少操作或资源标识。' : null,
    missingImpact ? '缺少影响摘要。' : null,
    missingPreflight ? '缺少完整预检证据。' : null,
    invalidChronology || notYetPrepared ? '预检时间证据无效。' : null,
    expired ? '预检已过期，请重新运行预检。' : null,
    scopeChanged ? '当前作用域已变化，请重新运行预检。' : null,
    invalidConfirmation ? '确认文本要求无效。' : null,
  ].filter((reason): reason is string => reason !== null);

  return (
    <Modal
      open={open}
      title={
        <Space>
          <AlertTriangle aria-hidden="true" color="var(--platform-danger, #b42318)" size={20} />
          <span>{safeDisplayText(title)}</span>
        </Space>
      }
      closable={!pending}
      keyboard={!pending}
      mask={{ closable: false }}
      destroyOnHidden
      zIndex={1100}
      width={{ xs: 'calc(100vw - 16px)', sm: 640 }}
      focusable={{ focusTriggerAfterClose: true, trap: true }}
      afterOpenChange={(isOpen) => {
        if (isOpen) cancelButton.current?.focus();
      }}
      onCancel={() => {
        if (!pending) onCancel();
      }}
      footer={(
        <Space wrap>
          {conflict && onResolveConflict ? (
            <Button disabled={pending} icon={<RefreshCw aria-hidden="true" size={16} />} onClick={() => onResolveConflict(conflict.status)}>
              重新加载并预检
            </Button>
          ) : null}
          <Button ref={cancelButton} disabled={pending} onClick={onCancel}>取消</Button>
          <Button danger type="primary" loading={pending} disabled={!canConfirm} onClick={onConfirm}>
            {pending ? '提交中…' : actionLabel}
          </Button>
        </Space>
      )}
    >
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="资源 ID"><Typography.Text code>{resourceId}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="动作">{safeDisplayText(actionLabel)}</Descriptions.Item>
        <Descriptions.Item label="资源版本">{preflight?.resourceVersion ?? '缺失'}</Descriptions.Item>
        <Descriptions.Item label="预检时间">
          {preflight && preparedAt !== null ? <time dateTime={preflight.preparedAt}>{preflight.preparedAt}</time> : '缺失'}
        </Descriptions.Item>
        <Descriptions.Item label="过期时间">
          {preflight && expiresAt !== null ? <time dateTime={preflight.expiresAt}>{preflight.expiresAt}</time> : '缺失'}
        </Descriptions.Item>
      </Descriptions>

      <Typography.Title level={5}>影响摘要</Typography.Title>
      <ul>
        {impacts.map((item, index) => <li key={`${index}-${item}`}>{safeDisplayText(item)}</li>)}
      </ul>

      {blockers.length > 0 ? (
        <Alert
          type="error"
          showIcon
          title="存在阻断原因"
          description={
            <ul>
              {blockers.map((item, index) => (
                <li key={`${index}-${item.code}`}>
                  <Typography.Text code>{item.code}</Typography.Text>：
                  <span>{safeDisplayText(item.message)}</span>
                </li>
              ))}
            </ul>
          }
        />
      ) : null}
      {failClosedReasons.length > 0 ? <Alert type="error" showIcon title={failClosedReasons.join(' ')} /> : null}
      {conflict ? (
        <Alert
          type="warning"
          showIcon
          title={conflict.status === 409 ? '资源状态已变化（409）。' : '资源版本不匹配（412）。'}
          description="当前确认已失效；请重新加载权威事实并再次运行预检。"
        />
      ) : null}

      {confirmation ? (
        <label>
          <Typography.Text strong>{confirmation.label ?? `请输入资源 ID ${confirmation.expectedText} 以确认`}</Typography.Text>
          <Input
            autoComplete="off"
            value={confirmationText}
            disabled={pending}
            status={confirmationText.length > 0 && !confirmationMatches ? 'error' : undefined}
            onChange={(event) => setConfirmationText(event.target.value)}
          />
        </label>
      ) : null}
    </Modal>
  );
}
