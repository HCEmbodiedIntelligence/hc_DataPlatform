/* eslint-disable react-refresh/only-export-components -- pointer helpers are part of this form's public error-mapping contract */
import { useEffect, useMemo, useRef } from 'react';
import type { JSX } from 'react';
import { useForm } from 'react-hook-form';
import type { FieldErrors, Resolver } from 'react-hook-form';
import type { AnnotationFormDefinition } from '../../../entities/annotation-schema';
import { compileAnnotationFormSchema } from '../../../entities/annotation-schema';

export interface StableFieldError { readonly path: string; readonly code: string; readonly message: string }

// The pointer helpers are exported here because they are part of the form's server-error contract.
// eslint-disable-next-line react-refresh/only-export-components
export function jsonPointerToFieldName(pointer: string): string | null {
  if (!pointer.startsWith('/')) return null;
  const parts = pointer.slice(1).split('/').map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'));
  if (!parts.length || parts.some((part) => !part || part === '__proto__' || part === 'constructor' || part === 'prototype')) return null;
  return parts.join('.');
}

/** Maps backend AnnotationEntry pointers onto the schema-driven editor fields. */
// eslint-disable-next-line react-refresh/only-export-components
export function annotationJsonPointerToFieldName(pointer: string): string | null {
  const generic = jsonPointerToFieldName(pointer);
  if (!generic) return null;
  const aliases: Readonly<Record<string, string>> = {
    'entries.0.semantic_type': 'semanticType',
    'entries.0.label_code': 'labelCode',
    'entries.0.anchor.start_ns': 'startNs',
    'entries.0.anchor.end_ns': 'endNs',
    'entries.0.anchor.at_ns': 'atNs',
  };
  if (aliases[generic]) return aliases[generic];
  const attribute = generic.match(/^entries\.0\.attributes\.(.+)$/)?.[1];
  return attribute ?? generic;
}

function zodResolver(schema: ReturnType<typeof compileAnnotationFormSchema>['schema']): Resolver<Record<string, unknown>> {
  return (values) => {
    const result = schema.safeParse(values);
    if (result.success) return { values: result.data, errors: {} };
    const errors: FieldErrors<Record<string, unknown>> = {};
    for (const issue of result.error.issues) {
      const key = issue.path.join('.');
      if (key && !errors[key]) errors[key] = { type: issue.code, message: issue.message };
    }
    return { values: {}, errors };
  };
}

export interface SchemaDrivenAnnotationFormProps {
  readonly definition: AnnotationFormDefinition;
  readonly defaultValues: Readonly<Record<string, unknown>>;
  readonly disabled?: boolean;
  readonly serverErrors?: readonly StableFieldError[];
  readonly onChange?: (values: Readonly<Record<string, unknown>>, dirty: boolean) => void;
  readonly onSubmit: (values: Readonly<Record<string, unknown>>) => void | Promise<void>;
  readonly onFormErrorSummary?: (messages: readonly string[]) => void;
}

export function SchemaDrivenAnnotationForm(props: SchemaDrivenAnnotationFormProps): JSX.Element {
  const { onChange, onFormErrorSummary, onSubmit, serverErrors } = props;
  const compiled = useMemo(() => compileAnnotationFormSchema(props.definition), [props.definition]);
  const form = useForm<Record<string, unknown>>({ defaultValues: { ...props.defaultValues }, resolver: zodResolver(compiled.schema), mode: 'onChange' });
  const previousServerFields = useRef<readonly string[]>([]);

  useEffect(() => {
    const subscription = form.watch((values) => onChange?.(values, form.formState.isDirty));
    return () => subscription.unsubscribe();
  }, [form, onChange]);

  useEffect(() => {
    const summary: string[] = [];
    if (previousServerFields.current.length) form.clearErrors([...previousServerFields.current]);
    const mappedFields: string[] = [];
    for (const error of serverErrors ?? []) {
      const fieldName = annotationJsonPointerToFieldName(error.path);
      if (fieldName && props.definition.fields.some((field) => field.key === fieldName)) {
        form.setError(fieldName, { type: error.code, message: error.message });
        mappedFields.push(fieldName);
      } else summary.push(error.message);
    }
    previousServerFields.current = mappedFields;
    onFormErrorSummary?.(summary);
  }, [form, onFormErrorSummary, props.definition.fields, serverErrors]);

  return (
    <form className="annotation-schema-form" noValidate onSubmit={(event) => { void form.handleSubmit(onSubmit)(event); }}>
      <h3>{props.definition.title}</h3>
      {props.definition.fields.map((field) => {
        const unsupported = compiled.unsupportedKeys.has(field.key);
        const disabled = props.disabled || field.readOnly || unsupported;
        const error = form.formState.errors[field.key];
        const describedBy = `${field.key}-description ${field.key}-error`;
        return (
          <div className="annotation-form-field" key={field.key} data-unknown-enum={unsupported || undefined}>
            <label htmlFor={field.key}>{field.label}{field.required ? ' *' : ''}</label>
            {field.description ? <small id={`${field.key}-description`}>{field.description}</small> : null}
            {unsupported ? (
              <output id={field.key} aria-describedby={describedBy}>未知字段类型 {field.type}，已只读保留：{JSON.stringify(props.defaultValues[field.key] ?? null)}</output>
            ) : field.type === 'boolean' ? (
              <input id={field.key} type="checkbox" disabled={disabled} aria-describedby={describedBy} {...form.register(field.key)} />
            ) : field.type === 'enum' ? (
              <select id={field.key} disabled={disabled} aria-describedby={describedBy} {...form.register(field.key)}>
                <option value="">请选择</option>
                {field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            ) : field.type === 'multi-enum' ? (
              <select id={field.key} multiple disabled={disabled} aria-describedby={describedBy} {...form.register(field.key)}>
                {field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            ) : field.type === 'time-range' ? (
              <fieldset disabled={disabled}><legend>{field.label}精确范围</legend><input aria-label="开始纳秒" {...form.register(`${field.key}.startNs`)} /><input aria-label="结束纳秒" {...form.register(`${field.key}.endNs`)} /></fieldset>
            ) : (
              <input id={field.key} type={field.type === 'number' ? 'number' : 'text'} disabled={disabled} aria-describedby={describedBy} {...form.register(field.key, field.type === 'number' ? { valueAsNumber: true } : {})} />
            )}
            <span id={`${field.key}-error`} role={error ? 'alert' : undefined}>{typeof error?.message === 'string' ? error.message : ''}</span>
          </div>
        );
      })}
      <button type="submit" disabled={props.disabled || compiled.unsupportedKeys.size > 0 || form.formState.isSubmitting || !form.formState.isValid}>应用到工作副本</button>
    </form>
  );
}
