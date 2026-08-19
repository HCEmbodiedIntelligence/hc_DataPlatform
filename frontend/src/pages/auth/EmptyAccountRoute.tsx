import { useState } from "react";
import { Alert } from "antd";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { AuthLayout } from "./AuthLayout";
import {
  EmptyAccountStatus,
  type EmptyAccountIntent,
} from "./EmptyAccountStatus";
import { EmptyAccountRequestPanel } from "./EmptyAccountRequestPanel";
import { getSessionBootstrap, logoutSession } from "./api";
import { authErrorMessage } from "./error-messages";
import { installSessionBootstrap } from "./runtime-scope";

const intentMessages: Readonly<Record<EmptyAccountIntent, string>> = {
  membership: "填写管理员提供的真实项目 ID，提交项目加入申请。",
  capability: "填写项目 ID 和最小 capability 集合，等待另一位管理员审批。",
  history: "审批后刷新正式 SessionBootstrap；本页不伪造全局申请历史。",
};

function isIntent(value: string | null): value is EmptyAccountIntent {
  return (
    value === "membership" || value === "capability" || value === "history"
  );
}

export function EmptyAccountRoute() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const principal = useShellStore((state) => state.principal);
  const sessionToken = useShellStore((state) => state.sessionToken);
  const setSession = useShellStore((state) => state.setSession);
  const clearSensitiveState = useShellStore(
    (state) => state.clearSensitiveState,
  );
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshSuccess, setRefreshSuccess] = useState<string | null>(null);
  const intentValue = searchParams.get("intent");
  const intent = isIntent(intentValue) ? intentValue : null;

  if (!principal || !sessionToken) {
    return <Navigate replace to="/auth/login" />;
  }

  function selectIntent(nextIntent: EmptyAccountIntent): void {
    setSearchParams({ intent: nextIntent }, { replace: true });
  }

  async function logout(): Promise<void> {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await logoutSession();
      clearSensitiveState();
      setSession(null, null);
      navigate("/auth/login", { replace: true });
    } catch (reason) {
      if (isDomainError(reason) && reason.httpStatus === 401) {
        clearSensitiveState();
        setSession(null, null);
        navigate("/auth/session-expired", { replace: true });
        return;
      }
      setLogoutError(authErrorMessage(reason, "logout"));
      setLoggingOut(false);
    }
  }

  async function refreshAccess(): Promise<void> {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshError(null);
    setRefreshSuccess(null);
    try {
      const bootstrap = await getSessionBootstrap();
      installSessionBootstrap(bootstrap);
      const capabilityCount = bootstrap.available_scopes.reduce(
        (count, scope) => count + scope.capabilities.length,
        0,
      );
      if (capabilityCount > 0) {
        navigate("/", { replace: true });
        return;
      }
      if (bootstrap.available_scopes.length > 0) {
        setSearchParams({ intent: "capability" }, { replace: true });
        setRefreshSuccess("项目加入已生效；请继续申请完成工作所需的最小权限。");
      } else {
        setRefreshSuccess("审批尚未生效；会话仍没有可用项目范围。");
      }
    } catch (reason) {
      setRefreshError(authErrorMessage(reason, "bootstrap"));
    } finally {
      setRefreshing(false);
    }
  }

  const notice = logoutError ? (
    <Alert type="error" showIcon title="退出未完成" description={logoutError} />
  ) : refreshError ? (
    <Alert type="error" showIcon title="刷新未完成" description={refreshError} />
  ) : refreshSuccess ? (
    <Alert
      type="success"
      showIcon
      title="会话范围已刷新"
      description={refreshSuccess}
    />
  ) : intent ? (
    <Alert
      type="info"
      showIcon
      title="正式申请流程"
      description={intentMessages[intent]}
    />
  ) : null;

  return (
    <AuthLayout
      rail={
        <EmptyAccountStatus
          username={principal.displayName}
          headingLevel={1}
          loggingOut={loggingOut}
          notice={notice}
          onIntent={selectIntent}
          onLogout={() => void logout()}
        />
      }
    >
      <EmptyAccountRequestPanel
        intent={intent}
        refreshing={refreshing}
        onRefresh={refreshAccess}
      />
    </AuthLayout>
  );
}
