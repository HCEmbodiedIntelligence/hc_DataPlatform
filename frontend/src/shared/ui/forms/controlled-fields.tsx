import { Checkbox, DatePicker, Form, Input, Select } from 'antd';
import type { CheckboxProps, DatePickerProps, InputProps, SelectProps } from 'antd';
import { useId, type ReactNode } from 'react';
import {
  useController,
  type Control,
  type FieldPath,
  type FieldPathValue,
  type FieldValues,
} from 'react-hook-form';

interface ControlledFieldProps<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
> {
  readonly control: Control<TFieldValues>;
  readonly name: TName;
  readonly label: ReactNode;
  readonly description?: ReactNode;
  readonly disabled?: boolean;
  readonly id?: string;
}

function fieldHelp(description: ReactNode, message: unknown): ReactNode {
  if (typeof message === 'string' && message.length > 0) return message;
  return description;
}

function useFieldId(explicitId: string | undefined): string {
  const generated = useId();
  return explicitId ?? `field-${generated.replaceAll(':', '')}`;
}

export type RHFInputProps<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
> = ControlledFieldProps<TFieldValues, TName> &
  Omit<InputProps, 'defaultValue' | 'id' | 'name' | 'onBlur' | 'onChange' | 'value'>;

export function RHFInput<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
>({ control, description, disabled, id: explicitId, label, name, ...inputProps }: RHFInputProps<TFieldValues, TName>) {
  const id = useFieldId(explicitId);
  const { field, fieldState } = useController({ control, name, disabled });
  return (
    <Form.Item
      htmlFor={id}
      label={label}
      help={fieldHelp(description, fieldState.error?.message)}
      validateStatus={fieldState.error ? 'error' : undefined}
    >
      <Input
        {...inputProps}
        id={id}
        name={field.name}
        value={field.value == null ? '' : String(field.value)}
        disabled={disabled}
        status={fieldState.error ? 'error' : undefined}
        onBlur={field.onBlur}
        onChange={field.onChange}
        ref={(instance) => field.ref(instance?.input ?? null)}
      />
    </Form.Item>
  );
}

export type RHFSelectProps<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
  TValue = FieldPathValue<TFieldValues, TName>,
> = ControlledFieldProps<TFieldValues, TName> &
  Omit<SelectProps<TValue>, 'defaultValue' | 'id' | 'onBlur' | 'onChange' | 'value'>;

export function RHFSelect<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
  TValue = FieldPathValue<TFieldValues, TName>,
>({ control, description, disabled, id: explicitId, label, name, ...selectProps }: RHFSelectProps<TFieldValues, TName, TValue>) {
  const id = useFieldId(explicitId);
  const { field, fieldState } = useController({ control, name, disabled });
  return (
    <Form.Item
      htmlFor={id}
      label={label}
      help={fieldHelp(description, fieldState.error?.message)}
      validateStatus={fieldState.error ? 'error' : undefined}
    >
      <Select<TValue>
        {...selectProps}
        id={id}
        value={field.value as TValue}
        disabled={disabled}
        status={fieldState.error ? 'error' : undefined}
        onBlur={field.onBlur}
        onChange={(value) => field.onChange(value)}
        ref={field.ref}
      />
    </Form.Item>
  );
}

export type RHFCheckboxProps<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
> = ControlledFieldProps<TFieldValues, TName> &
  Omit<CheckboxProps, 'checked' | 'defaultChecked' | 'id' | 'name' | 'onBlur' | 'onChange'>;

export function RHFCheckbox<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
>({ children, control, description, disabled, id: explicitId, label, name, ...checkboxProps }: RHFCheckboxProps<TFieldValues, TName>) {
  const id = useFieldId(explicitId);
  const { field, fieldState } = useController({ control, name, disabled });
  return (
    <Form.Item
      htmlFor={id}
      label={label}
      help={fieldHelp(description, fieldState.error?.message)}
      validateStatus={fieldState.error ? 'error' : undefined}
    >
      <Checkbox
        {...checkboxProps}
        id={id}
        name={field.name}
        checked={Boolean(field.value)}
        disabled={disabled}
        onBlur={field.onBlur}
        onChange={(event) => field.onChange(event.target.checked)}
        ref={field.ref}
      >
        {children}
      </Checkbox>
    </Form.Item>
  );
}

type AntDateValue = Parameters<NonNullable<DatePickerProps['onChange']>>[0];

export type RHFDatePickerProps<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
> = ControlledFieldProps<TFieldValues, TName> &
  Omit<DatePickerProps, 'defaultValue' | 'id' | 'onBlur' | 'onChange' | 'value'> & {
    readonly toPickerValue: (value: FieldPathValue<TFieldValues, TName>) => AntDateValue;
    readonly fromPickerValue: (value: AntDateValue) => FieldPathValue<TFieldValues, TName>;
  };

export function RHFDatePicker<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
>({
  control,
  description,
  disabled,
  fromPickerValue,
  id: explicitId,
  label,
  name,
  toPickerValue,
  ...datePickerProps
}: RHFDatePickerProps<TFieldValues, TName>) {
  const id = useFieldId(explicitId);
  const { field, fieldState } = useController({ control, name, disabled });
  return (
    <Form.Item
      htmlFor={id}
      label={label}
      help={fieldHelp(description, fieldState.error?.message)}
      validateStatus={fieldState.error ? 'error' : undefined}
    >
      <DatePicker
        {...datePickerProps}
        id={id}
        value={toPickerValue(field.value)}
        disabled={disabled}
        status={fieldState.error ? 'error' : undefined}
        onBlur={field.onBlur}
        onChange={(value) => field.onChange(fromPickerValue(value))}
        ref={field.ref}
      />
    </Form.Item>
  );
}
