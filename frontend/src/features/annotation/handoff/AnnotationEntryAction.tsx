import { useRef, useState } from 'react';
import type { JSX } from 'react';

export interface AnnotationSchemaOption {
  readonly schemaVersionId: string;
  readonly label: string;
  readonly resolutionId: string;
  readonly resolutionToken: string;
}
export type AnnotationEntryResolution =
  | { readonly kind: 'OPEN_EXISTING'; readonly taskId: string }
  | { readonly kind: 'CLAIMABLE'; readonly taskId: string; readonly etag: string }
  | { readonly kind: 'CAN_CREATE'; readonly schemaOptions: readonly AnnotationSchemaOption[] }
  | { readonly kind: 'ASSIGNED_TO_OTHER'; readonly message: string }
  | { readonly kind: 'FORBIDDEN'; readonly reasonCode: string };

export interface AnnotationEntryActionProps {
  readonly resolution: AnnotationEntryResolution;
  readonly onOpen: (taskId: string) => void;
  readonly onClaim: (resolution: Extract<AnnotationEntryResolution, { kind: 'CLAIMABLE' }>) => Promise<{ taskId: string }>;
  readonly onCreate: (option: AnnotationSchemaOption) => Promise<{ taskId: string }>;
}

export function AnnotationEntryAction(props: AnnotationEntryActionProps): JSX.Element | null {
  const [choosing, setChoosing] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const resolution = props.resolution;

  if (resolution.kind === 'FORBIDDEN') return null;
  if (resolution.kind === 'ASSIGNED_TO_OTHER') return <span role="status">{resolution.message}</span>;

  const run = async (operation: () => Promise<{ taskId: string }>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try { props.onOpen((await operation()).taskId); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '标注入口操作失败'); }
    finally { inFlight.current = false; setPending(false); }
  };

  if (resolution.kind === 'OPEN_EXISTING') return <button type="button" onClick={() => props.onOpen(resolution.taskId)}>继续标注</button>;
  if (resolution.kind === 'CLAIMABLE') return <><button type="button" disabled={pending} onClick={() => void run(() => props.onClaim(resolution))}>{pending ? '领取中…' : '领取并标注'}</button>{error ? <span role="alert">{error}</span> : null}</>;

  const only = resolution.schemaOptions.length === 1 ? resolution.schemaOptions[0] : undefined;
  if (only) return <><button type="button" disabled={pending} onClick={() => void run(() => props.onCreate(only))}>{pending ? '创建中…' : '开始标注'}</button>{error ? <span role="alert">{error}</span> : null}</>;
  return (
    <>
      <button type="button" disabled={pending || !resolution.schemaOptions.length} onClick={() => setChoosing(true)}>开始标注</button>
      {choosing ? <section role="dialog" aria-modal="true" aria-labelledby="schema-choice-title"><h2 id="schema-choice-title">选择标注结构</h2>{resolution.schemaOptions.map((option) => <button type="button" disabled={pending} key={option.schemaVersionId} onClick={() => void run(() => props.onCreate(option))}>{option.label}</button>)}<button type="button" onClick={() => setChoosing(false)}>取消</button></section> : null}
      {error ? <span role="alert">{error}</span> : null}
    </>
  );
}
