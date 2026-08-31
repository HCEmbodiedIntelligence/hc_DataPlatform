import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { getSessionBootstrap, logoutSession } from "../../pages/auth/api";
import { authErrorMessage } from "../../pages/auth/error-messages";
import {
  installSessionBootstrap,
  loadRuntimeAuthorization,
} from "../../pages/auth/runtime-scope";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { useToast } from "../providers/ToastProvider";
import type { PageAvailability } from "./navigation-manifest";
import { PlatformShell, type ScopeOption } from "./PlatformShell";

export function RuntimePlatformShell({
  pageAvailability,
}: {
  readonly pageAvailability: PageAvailability;
}) {
  const sessionToken = useShellStore((state) => state.sessionToken);
  const sessionScopes = useShellStore((state) => state.sessionScopes);
  const sessionOrganizations = useShellStore(
    (state) => state.sessionOrganizations,
  );
  const bootstrapLoaded = useShellStore((state) => state.bootstrapLoaded);
  const setAuthorizationLoading = useShellStore(
    (state) => state.setAuthorizationLoading,
  );
  const setAuthorizationFailed = useShellStore(
    (state) => state.setAuthorizationFailed,
  );
  const setSession = useShellStore((state) => state.setSession);
  const clearSensitiveState = useShellStore(
    (state) => state.clearSensitiveState,
  );
  const navigate = useNavigate();
  const { showToast } = useToast();
  const logoutInFlight = useRef(false);
  const [logoutPending, setLogoutPending] = useState(false);

  async function logout(): Promise<void> {
    if (logoutInFlight.current) return;
    logoutInFlight.current = true;
    setLogoutPending(true);
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
      showToast({
        title: "退出未完成",
        message: authErrorMessage(reason, "logout"),
        tone: "error",
      });
      logoutInFlight.current = false;
      setLogoutPending(false);
    }
  }

  useEffect(() => {
    if (!sessionToken || bootstrapLoaded) return;
    const controller = new AbortController();
    setAuthorizationLoading();
    void getSessionBootstrap({ signal: controller.signal })
      .then(installSessionBootstrap)
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        if (isDomainError(reason) && reason.httpStatus === 401) {
          setSession(null, null);
          navigate("/auth/session-expired", { replace: true });
          return;
        }
        setAuthorizationFailed();
      });
    return () => controller.abort();
  }, [
    bootstrapLoaded,
    sessionToken,
    navigate,
    setAuthorizationFailed,
    setAuthorizationLoading,
    setSession,
  ]);

  if (!sessionToken) {
    return <Navigate replace to="/auth/login" />;
  }

  const organizationNames = new Map(
    sessionOrganizations.map((organization) => [
      organization.organizationId,
      organization.organizationName,
    ]),
  );
  const scopeOptions: readonly ScopeOption[] = sessionScopes.flatMap(
    (grant) => {
      const common = {
        organizationId: grant.organizationId,
        organizationName:
          grant.organizationName ??
          organizationNames.get(grant.organizationId) ??
          grant.organizationId,
        projectId: grant.projectId,
        projectName: grant.projectName ?? grant.projectId,
        projectWide: grant.projectWide,
      } as const;
      return grant.regionCodes.length > 0
        ? grant.regionCodes.map((regionCode) => ({
            ...common,
            regionCode,
            regionName: regionCode,
          }))
        : [common];
    },
  );

  return (
    <PlatformShell
      authorizationLoader={loadRuntimeAuthorization}
      logoutPending={logoutPending}
      onLogout={() => void logout()}
      pageAvailability={pageAvailability}
      scopeOptions={scopeOptions}
    />
  );
}
