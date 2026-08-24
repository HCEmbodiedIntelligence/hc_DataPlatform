import { useState } from "react";
import { Form, Input } from "antd";
import { Eye, EyeOff, LockKeyhole } from "lucide-react";
import {
  useController,
  type Control,
  type FieldPath,
  type FieldValues,
} from "react-hook-form";
import styles from "../styles.module.css";

interface ControlledPasswordFieldProps<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
> {
  readonly control: Control<TFieldValues>;
  readonly name: TName;
  readonly id: string;
  readonly label: string;
  readonly autoComplete: "current-password" | "new-password";
  readonly placeholder: string;
  readonly disabled?: boolean;
  readonly description?: string;
}

export function ControlledPasswordField<
  TFieldValues extends FieldValues,
  TName extends FieldPath<TFieldValues>,
>({
  autoComplete,
  control,
  description,
  disabled = false,
  id,
  label,
  name,
  placeholder,
}: ControlledPasswordFieldProps<TFieldValues, TName>) {
  const [visible, setVisible] = useState(false);
  const { field, fieldState } = useController({ control, name, disabled });
  const help = fieldState.error?.message ?? description;

  return (
    <Form.Item
      help={help}
      htmlFor={id}
      label={label}
      validateStatus={fieldState.error ? "error" : undefined}
    >
      <Input
        id={id}
        autoComplete={autoComplete}
        disabled={disabled}
        name={field.name}
        placeholder={placeholder}
        prefix={<LockKeyhole aria-hidden="true" size={17} />}
        status={fieldState.error ? "error" : undefined}
        suffix={
          <button
            aria-label={visible ? `隐藏${label}` : `显示${label}`}
            aria-pressed={visible}
            className={styles.passwordToggle}
            disabled={disabled}
            type="button"
            onClick={() => setVisible((current) => !current)}
            onMouseDown={(event) => event.preventDefault()}
          >
            {visible ? (
              <EyeOff aria-hidden="true" size={17} />
            ) : (
              <Eye aria-hidden="true" size={17} />
            )}
          </button>
        }
        type={visible ? "text" : "password"}
        value={typeof field.value === "string" ? field.value : ""}
        onBlur={field.onBlur}
        onChange={field.onChange}
        ref={(instance) => field.ref(instance?.input ?? null)}
      />
    </Form.Item>
  );
}
