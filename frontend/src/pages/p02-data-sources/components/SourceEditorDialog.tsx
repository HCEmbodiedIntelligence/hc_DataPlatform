import { Alert, Button, Flex, Form, Input, Modal, type InputRef } from 'antd';
import { useEffect, useRef } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { z } from 'zod';
import type {
  KnownConnectorConfiguration,
  WritableConnectorBinding,
  WritableConnectorConfiguration,
} from '../../../features/ingest/connectors/registry';
import {
  createZodResolver,
  RHFInput,
  RHFSelect,
} from '../../../shared/ui';
import styles from '../styles.module.css';

export interface SourceDraft {
  readonly name: string;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly uploadPolicyCode: string;
  readonly configuration: WritableConnectorConfiguration;
  readonly binding: WritableConnectorBinding;
  readonly credentialToken?: string;
  readonly changeReason?: string;
}

export interface SourceEditorInitialValue {
  readonly name: string;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly uploadPolicyCode: string;
  readonly configuration: KnownConnectorConfiguration;
  readonly binding: WritableConnectorBinding;
}

const sourceEditorSchema = z
  .object({
    mode: z.enum(['create', 'update']),
    name: z.string().trim().min(1, '请输入名称').max(128, '名称最多 128 个字符'),
    kind: z.enum(['ROBOT', 'EDGE_AGENT', 'OSS_IMPORT']),
    sourceFormat: z.string().trim().min(1, '请输入来源格式'),
    sourceFormatVersion: z.string().trim(),
    uploadPolicyCode: z.string().trim().min(1, '请输入上传策略'),
    robotId: z.string().trim(),
    transport: z.enum(['HTTPS', 'MQTTS', 'OUTBOUND_HTTPS']),
    agentId: z.string().trim(),
    heartbeatPolicyId: z.string().trim(),
    sourceAlias: z.string().trim(),
    ossAccountAlias: z.string().trim(),
    bucketAlias: z.string().trim(),
    prefixHint: z.string().trim(),
    roleRef: z.string().trim(),
    sourceRegionCode: z.string().trim(),
    changeReason: z.string().trim().max(500, '变更原因最多 500 个字符'),
  })
  .superRefine((value, context) => {
    const requireField = (field: keyof typeof value, message: string) => {
      if (String(value[field]).length === 0) {
        context.addIssue({ code: 'custom', path: [field], message });
      }
    };
    if (value.kind === 'ROBOT') {
      requireField('robotId', '请输入机器人稳定 ID');
    } else if (value.kind === 'EDGE_AGENT') {
      requireField('agentId', '请输入 Agent 稳定 ID');
      requireField('heartbeatPolicyId', '请输入心跳策略 ID');
    } else {
      requireField('sourceAlias', '请输入来源别名');
      requireField('ossAccountAlias', '请输入 OSS 账户别名');
      requireField('bucketAlias', '请输入 Bucket 别名');
      requireField('prefixHint', '请输入 Prefix 安全提示');
      requireField('roleRef', '请输入角色引用');
      requireField('sourceRegionCode', '请输入来源 Region');
    }
    if (value.mode === 'update') requireField('changeReason', '请输入变更原因');
  });

type SourceFormValues = z.infer<typeof sourceEditorSchema>;

function defaults(
  mode: 'create' | 'update',
  initial?: SourceEditorInitialValue,
): SourceFormValues {
  const configuration = initial?.configuration;
  const binding = initial?.binding;
  return {
    mode,
    name: initial?.name ?? '',
    kind: configuration?.kind ?? 'ROBOT',
    sourceFormat: initial?.sourceFormat ?? 'LEROBOT_V2',
    sourceFormatVersion: initial?.sourceFormatVersion ?? '2.1',
    uploadPolicyCode: initial?.uploadPolicyCode ?? 'STANDARD',
    robotId: binding?.kind === 'ROBOT' ? binding.robotId : '',
    transport:
      configuration?.kind === 'ROBOT'
        ? configuration.transport
        : configuration?.kind === 'EDGE_AGENT'
          ? configuration.transport
          : 'HTTPS',
    agentId:
      binding?.kind === 'EDGE_AGENT'
        ? binding.agentId
        : configuration?.kind === 'EDGE_AGENT'
          ? configuration.agentId
          : '',
    heartbeatPolicyId:
      configuration?.kind === 'EDGE_AGENT' ? configuration.heartbeatPolicyId : '',
    sourceAlias: binding?.kind === 'OSS_IMPORT' ? binding.sourceAlias : '',
    ossAccountAlias:
      configuration?.kind === 'OSS_IMPORT' ? configuration.ossAccountAlias : '',
    bucketAlias: configuration?.kind === 'OSS_IMPORT' ? configuration.bucketAlias : '',
    prefixHint: configuration?.kind === 'OSS_IMPORT' ? configuration.prefixHint : '',
    roleRef: configuration?.kind === 'OSS_IMPORT' ? configuration.roleRef : '',
    sourceRegionCode:
      configuration?.kind === 'OSS_IMPORT' ? configuration.sourceRegionCode : '',
    changeReason: '',
  };
}

function makeConfiguration(value: SourceFormValues): WritableConnectorConfiguration {
  if (value.kind === 'ROBOT') {
    return {
      kind: value.kind,
      transport: value.transport === 'MQTTS' ? 'MQTTS' : 'HTTPS',
    };
  }
  if (value.kind === 'EDGE_AGENT') {
    return {
      kind: value.kind,
      agentId: value.agentId,
      transport: value.transport === 'MQTTS' ? 'MQTTS' : 'OUTBOUND_HTTPS',
      heartbeatPolicyId: value.heartbeatPolicyId,
    };
  }
  return {
    kind: value.kind,
    ossAccountAlias: value.ossAccountAlias,
    bucketAlias: value.bucketAlias,
    prefixHint: value.prefixHint,
    roleRef: value.roleRef,
    sourceRegionCode: value.sourceRegionCode,
  };
}

function makeBinding(value: SourceFormValues): WritableConnectorBinding {
  if (value.kind === 'ROBOT') return { kind: value.kind, robotId: value.robotId };
  if (value.kind === 'EDGE_AGENT') return { kind: value.kind, agentId: value.agentId };
  return { kind: value.kind, sourceAlias: value.sourceAlias };
}

function ConnectorFields({
  control,
  kind,
  pending,
}: {
  readonly control: ReturnType<typeof useForm<SourceFormValues>>['control'];
  readonly kind: SourceFormValues['kind'];
  readonly pending: boolean;
}) {
  if (kind === 'ROBOT') {
    return (
      <>
        <RHFInput control={control} name="robotId" label="机器人稳定 ID" disabled={pending} autoComplete="off" />
        <RHFSelect
          control={control}
          name="transport"
          label="传输"
          disabled={pending}
          options={[{ label: 'HTTPS', value: 'HTTPS' }, { label: 'MQTTS', value: 'MQTTS' }]}
        />
      </>
    );
  }
  if (kind === 'EDGE_AGENT') {
    return (
      <>
        <RHFInput control={control} name="agentId" label="Agent 稳定 ID" disabled={pending} autoComplete="off" />
        <RHFSelect
          control={control}
          name="transport"
          label="传输"
          disabled={pending}
          options={[
            { label: 'OUTBOUND_HTTPS', value: 'OUTBOUND_HTTPS' },
            { label: 'MQTTS', value: 'MQTTS' },
          ]}
        />
        <RHFInput control={control} name="heartbeatPolicyId" label="心跳策略 ID" disabled={pending} autoComplete="off" />
      </>
    );
  }
  return (
    <>
      <RHFInput control={control} name="sourceAlias" label="来源别名" disabled={pending} autoComplete="off" />
      <RHFInput control={control} name="ossAccountAlias" label="OSS 账户别名" disabled={pending} autoComplete="off" />
      <RHFInput control={control} name="bucketAlias" label="Bucket 别名" disabled={pending} autoComplete="off" />
      <RHFInput control={control} name="prefixHint" label="Prefix 安全提示" disabled={pending} autoComplete="off" />
      <RHFInput control={control} name="roleRef" label="角色引用" disabled={pending} autoComplete="off" />
      <RHFInput control={control} name="sourceRegionCode" label="来源 Region" disabled={pending} autoComplete="off" />
    </>
  );
}

export function SourceEditorDialog(props: {
  readonly open: boolean;
  readonly mode: 'create' | 'update';
  readonly pending: boolean;
  readonly initial?: SourceEditorInitialValue;
  readonly errorMessage?: string | null;
  readonly onClose: () => void;
  readonly onSubmit: (draft: SourceDraft) => void;
}) {
  const credentialRef = useRef<InputRef>(null);
  const form = useForm<SourceFormValues>({
    defaultValues: defaults(props.mode, props.initial),
    mode: 'onChange',
    resolver: createZodResolver(sourceEditorSchema),
  });
  const kind = form.watch('kind');

  useEffect(() => {
    if (!props.open) return;
    form.reset(defaults(props.mode, props.initial));
    if (credentialRef.current?.input) credentialRef.current.input.value = '';
  }, [form, props.initial, props.mode, props.open]);

  const close = () => {
    if (props.pending) return;
    form.reset(defaults(props.mode, props.initial));
    if (credentialRef.current?.input) credentialRef.current.input.value = '';
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title={props.mode === 'create' ? '新建数据源' : '编辑数据源'}
      footer={null}
      onCancel={close}
      closable={!props.pending}
      keyboard={!props.pending}
      mask={{ closable: false }}
      destroyOnHidden
      width={720}
      zIndex={1100}
      afterOpenChange={(open) => {
        if (open) form.setFocus('name');
      }}
    >
      <form
        className={styles.editorForm}
        noValidate
        onSubmit={(event) => {
          void form.handleSubmit((value) => {
            const credentialToken = credentialRef.current?.input?.value ?? '';
            props.onSubmit({
              name: value.name,
              sourceFormat: value.sourceFormat,
              sourceFormatVersion: value.sourceFormatVersion || null,
              uploadPolicyCode: value.uploadPolicyCode,
              configuration: makeConfiguration(value),
              binding: makeBinding(value),
              ...(credentialToken ? { credentialToken } : {}),
              ...(value.changeReason ? { changeReason: value.changeReason } : {}),
            });
            if (credentialRef.current?.input) credentialRef.current.input.value = '';
          })(event);
        }}
      >
        {props.errorMessage ? <Alert type="error" showIcon title={props.errorMessage} /> : null}
        <div className={styles.formGrid}>
          <RHFInput control={form.control} name="name" label="名称" disabled={props.pending} autoComplete="off" />
          <RHFSelect
            control={form.control}
            name="kind"
            label="连接器类型"
            disabled={props.pending || props.mode === 'update'}
            preserveValueWhenDisabled={props.mode === 'update'}
            options={[
              { label: '机器人', value: 'ROBOT' },
              { label: '边缘代理', value: 'EDGE_AGENT' },
              { label: 'OSS 导入', value: 'OSS_IMPORT' },
            ]}
          />
          <RHFInput control={form.control} name="sourceFormat" label="来源格式" disabled={props.pending} />
          <RHFInput control={form.control} name="sourceFormatVersion" label="格式版本" disabled={props.pending} />
          <RHFInput control={form.control} name="uploadPolicyCode" label="上传策略" disabled={props.pending} />
          <ConnectorFields control={form.control} kind={kind} pending={props.pending} />
          {props.mode === 'create' ? (
            <Form.Item
              className={styles.fullWidthField}
              label="访问 Token（可选）"
              help="仅当数据源需要 Token 认证时填写。Token 仅随本次请求提交，页面不会回显或保存到浏览器缓存。"
            >
              <Input.Password
                ref={credentialRef}
                name="credentialToken"
                aria-label="访问 Token（可选）"
                autoComplete="new-password"
                data-sensitive="credential"
                disabled={props.pending}
                visibilityToggle={false}
              />
            </Form.Item>
          ) : (
            <Controller
              control={form.control}
              name="changeReason"
              render={({ field, fieldState }) => (
                <Form.Item
                  className={styles.fullWidthField}
                  label="变更原因"
                  help={fieldState.error?.message}
                  validateStatus={fieldState.error ? 'error' : undefined}
                >
                  <Input.TextArea
                    {...field}
                    aria-label="变更原因"
                    rows={3}
                    maxLength={500}
                    disabled={props.pending}
                    status={fieldState.error ? 'error' : undefined}
                  />
                </Form.Item>
              )}
            />
          )}
        </div>
        <Flex justify="end" gap="small" wrap="wrap">
          <Button onClick={close} disabled={props.pending}>取消</Button>
          <Button type="primary" htmlType="submit" loading={props.pending}>
            {props.mode === 'create' ? '创建' : '保存'}
          </Button>
        </Flex>
      </form>
    </Modal>
  );
}
