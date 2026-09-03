import { Button, Drawer, Flex } from 'antd';
import { ChevronDown, ChevronUp, Filter, RotateCcw } from 'lucide-react';
import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
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
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  collapseOnApply?: boolean;
  activeFilterCount?: number;
  collapsedSummary?: string;
}

export function FilterToolbar({
  children,
  onApply,
  onReset,
  actions,
  label = '筛选条件',
  applyLabel = '应用筛选',
  disabled = false,
  collapsible = false,
  defaultCollapsed = false,
  collapseOnApply = false,
  activeFilterCount = 0,
  collapsedSummary,
}: Readonly<FilterToolbarProps>) {
  const compact = useCompactLayout();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const panelId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onApply?.();
    if (compact) setOpen(false);
    if (!compact && collapsible && collapseOnApply) setCollapsed(true);
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

  if (!compact && !collapsible) {
    return <section className={styles.filterToolbar}>{form}</section>;
  }

  if (!compact) {
    return (
      <section
        className={`${styles.filterToolbar} ${styles.filterToolbarCollapsible}`}
        aria-labelledby={`${panelId}-title`}
        data-filter-toolbar="collapsible"
      >
        <div className={styles.collapsibleHeader}>
          <div className={styles.filterHeading}>
            <h2 id={`${panelId}-title`}>
              {activeFilterCount > 0 ? `${label}（${activeFilterCount}）` : label}
            </h2>
            {collapsed ? (
              <span className={styles.filterSummary} title={collapsedSummary}>
                {collapsedSummary ??
                  (activeFilterCount > 0 ? '已有筛选条件生效' : '当前显示全部数据')}
              </span>
            ) : null}
          </div>
          <Button
            type="text"
            className={styles.filterToggle}
            aria-expanded={!collapsed}
            aria-controls={panelId}
            icon={
              collapsed ? (
                <ChevronDown aria-hidden="true" size={16} />
              ) : (
                <ChevronUp aria-hidden="true" size={16} />
              )
            }
            onClick={() => setCollapsed((current) => !current)}
          >
            {collapsed ? '展开' : '收起'}
          </Button>
        </div>
        <div id={panelId} className={styles.collapsibleBody} hidden={collapsed}>
          {form}
        </div>
      </section>
    );
  }

  return (
    <section className={styles.filterToolbarCompact} aria-label={label}>
      <Button
        ref={triggerRef}
        icon={<Filter aria-hidden="true" size={16} />}
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        {activeFilterCount > 0 ? `筛选（${activeFilterCount}）` : '筛选'}
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
