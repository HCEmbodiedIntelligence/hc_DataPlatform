import { useState } from "react";
import { Form, Input, type FormItemProps } from "antd";
import { Eye, EyeOff, LockKeyhole } from "lucide-react";
import styles from "./styles.module.css";

interface PasswordFieldProps {
  readonly id: string;
  readonly name: string;
  readonly label: string;
  readonly autoComplete: "current-password" | "new-password";
  readonly disabled?: boolean;
  readonly placeholder: string;
  readonly dependencies?: string[];
  readonly rules?: FormItemProps["rules"];
}

export function PasswordField({
  id,
  name,
  label,
  autoComplete,
  disabled = false,
  placeholder,
  dependencies,
  rules,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  return (
    <Form.Item
      name={name}
      label={label}
      htmlFor={id}
      dependencies={dependencies}
      rules={rules}
    >
      <Input
        id={id}
        name={name}
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        disabled={disabled}
        placeholder={placeholder}
        prefix={<LockKeyhole size={17} aria-hidden="true" />}
        suffix={
          <button
            className={styles.passwordToggle}
            type="button"
            aria-label={visible ? `隐藏${label}` : `显示${label}`}
            aria-pressed={visible}
            disabled={disabled}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => setVisible((current) => !current)}
          >
            {visible ? (
              <EyeOff size={17} aria-hidden="true" />
            ) : (
              <Eye size={17} aria-hidden="true" />
            )}
          </button>
        }
      />
    </Form.Item>
  );
}
