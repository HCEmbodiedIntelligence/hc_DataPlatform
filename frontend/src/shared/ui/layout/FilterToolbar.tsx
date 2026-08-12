import { Button, Drawer, Flex } from 'antd';
import { Filter, RotateCcw } from 'lucide-react';
import { useRef, useState, type FormEvent, type ReactNode } from 'react';
import styles from './layout.module.css';
import { useCompactLayout } from './responsive';

export interface FilterToolbarProps {
  children: ReactNode;
  onApply?: () => void;
  onReset?: () => void;
  actions?: ReactNode;
  label?: string;
  applyLabel?: string;
  disabled?: boolean;
}

export function FilterToolbar({
  children,
  onApply,
  onReset,
  actions,
  label = '筛选条件',
  applyLabel = '应用筛选',
  disabled = false,
}: Readonly<FilterToolbarProps>) {
  const compact = useCompactLayout();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onApply?.();
    if (compact) setOpen(false);
  };

  const form = (
    <form className={styles.filterForm} aria-label={label} onSubmit={submit}>
      <div className={styles.filterFields}>{children}</div>
      <Flex className={styles.filterActions} gap="small" wrap="wrap">
        {onReset ? (
          <Button
            type="default"
            icon={<RotateCcw aria-hidden="true" size={16} />}
            onClick={onReset}
            disabled={disabled}
          >
            重置
          </Button>
        ) : null}
        {onApply ? (
          <Button type="primary" htmlType="submit" disabled={disabled}>
            {applyLabel}
          </Button>
        ) : null}
        {actions}
      </Flex>
    </form>
  );

  if (!compact) return <section className={styles.filterToolbar}>{form}</section>;

  return (
    <section className={styles.filterToolbarCompact} aria-label={label}>
      <Button
        ref={triggerRef}
        icon={<Filter aria-hidden="true" size={16} />}
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        筛选
      </Button>
      <Drawer
        title={label}
        open={open}
        onClose={() => setOpen(false)}
        size="100%"
        destroyOnHidden
        afterOpenChange={(nextOpen) => {
          if (!nextOpen) triggerRef.current?.focus();
        }}
      >
        {form}
      </Drawer>
    </section>
  );
}
