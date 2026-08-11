import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

export interface ToastInput {
  title: string;
  message?: string;
  tone?: 'info' | 'success' | 'warning' | 'error';
}

interface ToastRecord extends ToastInput {
  id: number;
}

interface ToastContextValue {
  showToast: (toast: ToastInput) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<readonly ToastRecord[]>([]);
  const showToast = useCallback((toast: ToastInput) => {
    const id = Date.now();
    setToasts((current) => [...current, { ...toast, id }]);
    globalThis.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 5_000);
  }, []);
  const value = useMemo(() => ({ showToast }), [showToast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-viewport" aria-live="polite" aria-label="通知">
        {toasts.map((toast) => (
          <div className={`toast toast--${toast.tone ?? 'info'}`} key={toast.id} role="status">
            <strong>{toast.title}</strong>
            {toast.message ? <p>{toast.message}</p> : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// Provider and hook are intentionally colocated to keep the context private.
// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === null) throw new Error('useToast must be used inside ToastProvider');
  return context;
}
