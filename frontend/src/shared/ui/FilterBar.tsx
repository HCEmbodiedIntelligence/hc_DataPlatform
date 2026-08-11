import type { FormEvent, ReactNode } from 'react';

export interface FilterBarProps {
  children: ReactNode;
  onSubmit?: () => void;
  actions?: ReactNode;
  label?: string;
}

export function FilterBar({ children, onSubmit, actions, label = '筛选条件' }: FilterBarProps) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit?.();
  };
  return (
    <form className="filter-bar" aria-label={label} onSubmit={submit}>
      <div className="filter-bar__fields">{children}</div>
      {actions ? <div className="filter-bar__actions">{actions}</div> : null}
    </form>
  );
}
