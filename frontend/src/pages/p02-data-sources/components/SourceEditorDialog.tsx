import { useEffect, useRef, useState } from 'react';
import type {
  KnownConnectorConfiguration,
  WritableConnectorBinding,
} from '../../../features/ingest/connectors/registry';
import { formRawText, formText } from '../../../features/ingest/form-data';

export interface SourceDraft {
  readonly name: string;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly uploadPolicyCode: string;
  readonly configuration: KnownConnectorConfiguration;
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

function value(data: FormData, key: string): string {
  return formText(data, key);
}

function makeConfiguration(kind: KnownConnectorConfiguration['kind'], data: FormData): KnownConnectorConfiguration {
  if (kind === 'ROBOT') {
    return {
      kind,
      transport: value(data, 'transport') === 'MQTTS' ? 'MQTTS' : 'HTTPS',
      endpointRef: value(data, 'endpointRef'),
      safeEndpointHint: null,
      tlsProfileId: value(data, 'tlsProfileId') || null,
    };
  }
  if (kind === 'EDGE_AGENT') {
    return {
      kind,
      agentId: value(data, 'agentId'),
      transport: value(data, 'transport') === 'MQTTS' ? 'MQTTS' : 'OUTBOUND_HTTPS',
      heartbeatPolicyId: value(data, 'heartbeatPolicyId'),
    };
  }
  return {
    kind,
    ossAccountAlias: value(data, 'ossAccountAlias'),
    bucketAlias: value(data, 'bucketAlias'),
    prefixHint: value(data, 'prefixHint'),
    roleRef: value(data, 'roleRef'),
    sourceRegionCode: value(data, 'sourceRegionCode'),
  };
}

function makeBinding(kind: KnownConnectorConfiguration['kind'], data: FormData): WritableConnectorBinding {
  if (kind === 'ROBOT') return { kind, robotId: value(data, 'robotId') };
  if (kind === 'EDGE_AGENT') return { kind, agentId: value(data, 'agentId') };
  return { kind, sourceAlias: value(data, 'sourceAlias') };
}

function ConnectorFields(props: {
  readonly kind: KnownConnectorConfiguration['kind'];
  readonly initial?: SourceEditorInitialValue;
}) {
  const configuration = props.initial?.configuration;
  const binding = props.initial?.binding;
  if (props.kind === 'ROBOT') {
    const config = configuration?.kind === 'ROBOT' ? configuration : null;
    const robotBinding = binding?.kind === 'ROBOT' ? binding : null;
    return <>
      <label>机器人稳定 ID<input name="robotId" required defaultValue={robotBinding?.robotId} autoComplete="off" /></label>
      <label>传输<select name="transport" defaultValue={config?.transport ?? 'HTTPS'}><option>HTTPS</option><option>MQTTS</option></select></label>
      <label>Endpoint 引用<input name="endpointRef" required defaultValue={config?.endpointRef} autoComplete="off" /></label>
      <label>TLS Profile ID<input name="tlsProfileId" defaultValue={config?.tlsProfileId ?? ''} autoComplete="off" /></label>
    </>;
  }
  if (props.kind === 'EDGE_AGENT') {
    const config = configuration?.kind === 'EDGE_AGENT' ? configuration : null;
    const edgeBinding = binding?.kind === 'EDGE_AGENT' ? binding : null;
    return <>
      <label>Agent 稳定 ID<input name="agentId" required defaultValue={edgeBinding?.agentId ?? config?.agentId} autoComplete="off" /></label>
      <label>传输<select name="transport" defaultValue={config?.transport ?? 'OUTBOUND_HTTPS'}><option>OUTBOUND_HTTPS</option><option>MQTTS</option></select></label>
      <label>心跳策略 ID<input name="heartbeatPolicyId" required defaultValue={config?.heartbeatPolicyId} autoComplete="off" /></label>
    </>;
  }
  const config = configuration?.kind === 'OSS_IMPORT' ? configuration : null;
  const ossBinding = binding?.kind === 'OSS_IMPORT' ? binding : null;
  return <>
    <label>来源别名<input name="sourceAlias" required defaultValue={ossBinding?.sourceAlias} autoComplete="off" /></label>
    <label>OSS 账户别名<input name="ossAccountAlias" required defaultValue={config?.ossAccountAlias} autoComplete="off" /></label>
    <label>Bucket 别名<input name="bucketAlias" required defaultValue={config?.bucketAlias} autoComplete="off" /></label>
    <label>Prefix 安全提示<input name="prefixHint" required defaultValue={config?.prefixHint} autoComplete="off" /></label>
    <label>角色引用<input name="roleRef" required defaultValue={config?.roleRef} autoComplete="off" /></label>
    <label>来源 Region<input name="sourceRegionCode" required defaultValue={config?.sourceRegionCode} autoComplete="off" /></label>
  </>;
}

export function SourceEditorDialog(props: {
  readonly open: boolean;
  readonly mode: 'create' | 'update';
  readonly pending: boolean;
  readonly initial?: SourceEditorInitialValue;
  readonly onClose: () => void;
  readonly onSubmit: (draft: SourceDraft) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [kind, setKind] = useState<KnownConnectorConfiguration['kind']>(props.initial?.configuration.kind ?? 'ROBOT');
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (props.open && !dialog.open) {
      setKind(props.initial?.configuration.kind ?? 'ROBOT');
      dialog.showModal();
    }
    if (!props.open && dialog.open) dialog.close();
  }, [props.initial, props.open]);

  return (
    <dialog ref={dialogRef} onClose={props.onClose} aria-labelledby="source-editor-title">
      <form
        ref={formRef}
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const credentialToken = formRawText(data, 'credentialToken');
          const changeReason = value(data, 'changeReason');
          props.onSubmit({
            name: value(data, 'name'),
            sourceFormat: value(data, 'sourceFormat'),
            sourceFormatVersion: value(data, 'sourceFormatVersion') || null,
            uploadPolicyCode: value(data, 'uploadPolicyCode'),
            configuration: makeConfiguration(kind, data),
            binding: makeBinding(kind, data),
            ...(credentialToken ? { credentialToken } : {}),
            ...(changeReason ? { changeReason } : {}),
          });
          const field = event.currentTarget.elements.namedItem('credentialToken');
          if (field instanceof HTMLInputElement) field.value = '';
        }}
      >
        <h2 id="source-editor-title">{props.mode === 'create' ? '新建数据源' : '编辑数据源'}</h2>
        {props.mode === 'update' ? <p>稳定 ID 和连接器类型不可修改；秘密字段不会载入表单。</p> : null}
        <label>名称<input name="name" required maxLength={128} defaultValue={props.initial?.name} autoComplete="off" /></label>
        <label>连接器类型<select name="kind" value={kind} disabled={props.mode === 'update'} onChange={(event) => setKind(event.currentTarget.value as KnownConnectorConfiguration['kind'])}><option value="ROBOT">机器人</option><option value="EDGE_AGENT">边缘代理</option><option value="OSS_IMPORT">OSS 导入</option></select></label>
        <label>来源格式<input name="sourceFormat" required defaultValue={props.initial?.sourceFormat ?? 'LEROBOT_V2'} /></label>
        <label>格式版本<input name="sourceFormatVersion" defaultValue={props.initial?.sourceFormatVersion ?? '2.1'} /></label>
        <label>上传策略<input name="uploadPolicyCode" required defaultValue={props.initial?.uploadPolicyCode ?? 'STANDARD'} /></label>
        <ConnectorFields key={kind} kind={kind} initial={props.initial} />
        {props.mode === 'create' ? <>
          <label>访问 Token（可选）<input name="credentialToken" type="password" autoComplete="new-password" data-sensitive="credential" /></label>
          <p>凭据只在本次提交的 mutation closure 内使用，不回显、不持久化、不进入遥测或错误报告。</p>
        </> : <label>变更原因<textarea name="changeReason" required maxLength={500} /></label>}
        <div className="dialog-actions">
          <button type="button" onClick={() => { formRef.current?.reset(); props.onClose(); }} disabled={props.pending}>取消</button>
          <button type="submit" disabled={props.pending}>{props.pending ? '提交中…' : props.mode === 'create' ? '创建' : '保存'}</button>
        </div>
      </form>
    </dialog>
  );
}
