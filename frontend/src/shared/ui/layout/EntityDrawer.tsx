import { Drawer, Skeleton } from 'antd';
import { useRef, type ReactNode, type RefObject } from 'react';
import styles from './layout.module.css';
import { useCompactLayout } from './responsive';

export interface EntityDrawerProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  extra?: ReactNode;
  loading?: boolean;
  width?: number | string;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function EntityDrawer({
  open,
  title,
  onClose,
  children,
  footer,
  extra,
  loading = false,
  width = 440,
  returnFocusRef,
}: Readonly<EntityDrawerProps>) {
  const compact = useCompactLayout();
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);

  if (open && !wasOpenRef.current && document.activeElement instanceof HTMLElement) {
    previousFocusRef.current = document.activeElement;
  }
  wasOpenRef.current = open;

  return (
    <Drawer
      className={styles.entityDrawer}
      title={title}
      open={open}
      onClose={onClose}
      size={compact ? '100%' : width}
      footer={footer}
      extra={extra}
      keyboard
      mask={{ closable: true }}
      destroyOnHidden
      afterOpenChange={(nextOpen) => {
        if (!nextOpen) {
          (returnFocusRef?.current ?? previousFocusRef.current)?.focus();
          previousFocusRef.current = null;
        }
      }}
    >
      {loading ? <Skeleton active paragraph={{ rows: 7 }} aria-label="详情加载中" /> : children}
    </Drawer>
  );
}
