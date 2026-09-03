import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { trapTabKey } from './focus-trap';

export interface SideDrawerProps {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
}

export function SideDrawer({ open, title, children, onClose, footer }: SideDrawerProps) {
  const id = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => previousFocus.current?.focus();
  }, [open]);
  if (!open) return null;
  return (
    <aside ref={drawerRef} className="side-drawer" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} onKeyDown={(event) => {
      trapTabKey(event, drawerRef);
      if (event.key === 'Escape') onClose();
    }}>
      <header>
        <h2 id={`${id}-title`}>{title}</h2>
        <button ref={closeRef} className="icon-button" type="button" title="关闭抽屉" aria-label="关闭抽屉" onClick={onClose}>
          <X aria-hidden="true" />
        </button>
      </header>
      <div className="side-drawer__content">{children}</div>
      {footer ? <footer>{footer}</footer> : null}
    </aside>
  );
}
