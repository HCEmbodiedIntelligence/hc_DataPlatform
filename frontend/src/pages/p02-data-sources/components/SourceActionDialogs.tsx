import { Alert, Button, Flex, Form, Input, Modal, Typography, type InputRef } from 'antd';
import { useRef } from 'react';
import type { BlockedReason } from '../../../entities/data-source';
import { formRawText, formText } from '../../../features/ingest/form-data';
import {
  DangerConfirmModal,
  type DangerConflict,
  type DangerPreflightEvidence,
} from '../../../shared/ui';
import styles from '../styles.module.css';

export function ConfirmSourceStateDialog(props: {
  readonly open: boolean;
  readonly sourceId: string;
  readonly action: 'enable' | 'disable';
  readonly pending: boolean;
  readonly blockedReasons: readonly BlockedReason[];
  readonly preflight: DangerPreflightEvidence | null;
  readonly currentScopeKey: string;
  readonly conflict?: DangerConflict | null;
  readonly onResolveConflict?: (status: 409 | 412) => void;
  readonly onConfirm: () => void;
  readonly onClose: () => void;
}) {
  const enabling = props.action === 'enable';
  return (
    <DangerConfirmModal
      open={props.open}
      title={enabling ? '确认启用数据源' : '确认停用数据源'}
      actionLabel={enabling ? '确认启用' : '确认停用'}
      resourceId={props.sourceId}
      impact={
        enabling
          ? ['允许符合当前策略的新上传会话。', '历史上传事实保持不变。']
          : ['阻止创建新的上传会话。', '历史上传事实保持不变。']
      }
      blockers={props.blockedReasons}
      preflight={props.preflight}
      currentScopeKey={props.currentScopeKey}
      confirmation={{ expectedText: props.sourceId }}
      pending={props.pending}
      conflict={props.conflict}
      onCancel={props.onClose}
      onConfirm={props.onConfirm}
      onResolveConflict={props.onResolveConflict}
    />
  );
}

/** Future DELETE is fail-closed until a server contract and preflight are frozen. */
export function ConnectorDeleteConfirmDialog(props: {
  readonly open: boolean;
  readonly sourceId: string;
  readonly blockedReasons: readonly BlockedReason[];
  readonly currentScopeKey: string;
  readonly onClose: () => void;
}) {
  return (
    <DangerConfirmModal
      open={props.open}
      title="确认删除连接器"
      actionLabel="确认删除"
      resourceId={props.sourceId}
      impact={[
        '删除会阻止新会话且可能影响引用。',
        '历史上传事实不会被乐观移除。',
      ]}
      blockers={[
        ...props.blockedReasons,
        { code: 'CONTRACT_NOT_FROZEN', message: '后端尚未提供删除合同，当前禁止提交。' },
      ]}
      preflight={null}
      currentScopeKey={props.currentScopeKey}
      confirmation={{ expectedText: props.sourceId }}
      onCancel={props.onClose}
      onConfirm={() => undefined}
    />
  );
}

export function CredentialRotationDialog(props: {
  readonly open: boolean;
  readonly sourceId: string;
  readonly pending: boolean;
  readonly errorMessage?: string | null;
  readonly onClose: () => void;
  readonly onConfirm: (token: string, reason: string) => void;
}) {
  const tokenRef = useRef<InputRef>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const clearAndClose = () => {
    if (props.pending) return;
    formRef.current?.reset();
    if (tokenRef.current?.input) tokenRef.current.input.value = '';
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title="轮换数据源凭据"
      footer={null}
      onCancel={clearAndClose}
      closable={!props.pending}
      keyboard={!props.pending}
      mask={{ closable: false }}
      destroyOnHidden
      zIndex={1100}
      afterOpenChange={(open) => {
        if (open) tokenRef.current?.focus();
      }}
    >
      <form
        ref={formRef}
        className={styles.credentialForm}
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const token = formRawText(data, 'credentialToken');
          const reason = formText(data, 'reason');
          if (!token || !reason) return;
          props.onConfirm(token, reason);
          if (tokenRef.current?.input) tokenRef.current.input.value = '';
        }}
      >
        <Typography.Paragraph>
          稳定 ID：<code>{props.sourceId}</code>
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary">
          当前凭据仅显示“已配置”和掩码；新 Token 不会回显、缓存、持久化或进入遥测。
        </Typography.Paragraph>
        {props.errorMessage ? <Alert type="error" showIcon title={props.errorMessage} /> : null}
        <Form.Item label="新 Token" required>
          <Input.Password
            ref={tokenRef}
            name="credentialToken"
            aria-label="新 Token"
            required
            autoComplete="new-password"
            data-sensitive="credential"
            disabled={props.pending}
            visibilityToggle={false}
          />
        </Form.Item>
        <Form.Item label="轮换原因" required>
          <Input.TextArea
            name="reason"
            aria-label="轮换原因"
            required
            rows={3}
            maxLength={500}
            disabled={props.pending}
          />
        </Form.Item>
        <Flex justify="end" gap="small" wrap="wrap">
          <Button disabled={props.pending} onClick={clearAndClose}>取消</Button>
          <Button type="primary" htmlType="submit" loading={props.pending}>确认轮换</Button>
        </Flex>
      </form>
    </Modal>
  );
}
