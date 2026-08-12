import { App as AntApp } from 'antd';
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from 'react';

export interface ToastInput {
  title: string;
  message?: string;
  tone?: 'info' | 'success' | 'warning' | 'error';
}

interface ToastContextValue {
  showToast: (toast: ToastInput) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);
const hiddenSensitiveDetail = '敏感详情已隐藏。请通过受控详情页查看。';
const sensitiveToastValue =
  /(?:bearer\s+[a-z0-9._~+/-]+=*|eyj[a-z0-9_-]*\.[a-z0-9_-]+\.[a-z0-9_-]+|(?:akia|asia)[a-z0-9]{16}|(?:token|secret|signature|credential|password|access[_-]?key)\s*[:=]|(?:密钥|口令|密码|访问令牌)\s*[:：=]|[?&](?:x-amz-[^=&#\s]*|signature|token|expires)=[^&#\s]+|[a-z][a-z0-9+.-]*:\/\/\S+|(?:^|\s)(?:[a-z]:\\|\/(?:[^/\s]+\/)+)\S*|(?:^|\s)(?:[a-z0-9._-]+\/){2,}[a-z0-9._-]+)/iu;

function safeToastText(value: string): string {
  return sensitiveToastValue.test(value) ? hiddenSensitiveDetail : value;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const { notification } = AntApp.useApp();
  const showToast = useCallback(
    (toast: ToastInput) => {
      const tone = toast.tone ?? 'info';
      notification[tone]({
        title: safeToastText(toast.title),
        description: toast.message ? safeToastText(toast.message) : undefined,
        duration: 5,
        placement: 'bottomRight',
        pauseOnHover: true,
        role: tone === 'error' ? 'alert' : 'status',
      });
    },
    [notification],
  );
  useEffect(() => () => notification.destroy(), [notification]);
  const value = useMemo(() => ({ showToast }), [showToast]);
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

// Provider and hook are intentionally colocated to keep the context private.
// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === null) throw new Error('useToast must be used inside ToastProvider');
  return context;
}
