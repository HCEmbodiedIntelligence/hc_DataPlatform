import { useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useScopeQueryRevision } from "../../shared/api/use-scoped-query";
import {
  getSessionBootstrap,
  logoutSession,
} from "../../features/account/session/api";
import { authErrorMessage } from "../../features/account/session/error-messages";
import { useSessionActivity } from "../../features/account/session/use-session-activity";
import {
  installSessionBootstrap,
  loadRuntimeAuthorization,
} from "../../features/account/session/runtime-scope";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { dataAnnotationRoutes } from "../../shared/routing/navigation-routes";
import { useToast } from "../../shared/ui/ToastProvider";
import type { PageAvailability } from "./navigation-manifest";
import { PlatformShell, type ScopeOption } from "./PlatformShell";

export function RuntimePlatformShell({
  pageAvailability,
}: {
  readonly pageAvailability: PageAvailability;
}) {
  const sessionToken = useShellStore((state) => state.sessionToken);
  const sessionRevision = useShellStore((state) => state.sessionRevision);
  const sessionExpired = useShellStore((state) => state.sessionExpired);
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
  const { pathname } = useLocation();
  const { showToast } = useToast();
  const logoutInFlight = useRef(false);
  const [logoutPending, setLogoutPending] = useState(false);
  useSessionActivity(bootstrapLoaded && !logoutPending);
  const client = useQueryClient();
  const scope = useShellStore(state => state.scope);
  const revision = useScopeQueryRevision();
  const capabilities = useCapabilities();
  const warmModel = pathname === dataAnnotationRoutes.annotate &&
    bootstrapLoaded && !capabilities.loading && !capabilities.failed &&
    ["annotation_task.read", "episode.read", "dataset_version.read", "robot_model.read"].every(capabilities.has);

  useEffect(() => {
    if (!warmModel || !scope?.projectId || !scope.regionCode) return;
    const controller = new AbortController();
    const selected = { organizationId: scope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode };
    // Warm a model only while choosing an annotation task. Other pages have no
    // reason to load annotation documents or dozens of private mesh files.
    void import("../../features/annotation/runtime/task-preload")
      .then(module => module.preloadHomeModel(client, selected, revision, controller.signal))
      .catch(() => undefined); // Foreground viewer has an explicit retry action.
    return () => controller.abort();
  }, [client, warmModel, revision, scope?.organizationId, scope?.projectId, scope?.regionCode]);

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
      .then((bootstrap) => {
        const current = useShellStore.getState();
        if (
          controller.signal.aborted ||
          current.sessionToken !== sessionToken ||
          current.sessionRevision !== sessionRevision
        )
          return;
        installSessionBootstrap(bootstrap);
      })
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
    sessionRevision,
    navigate,
    setAuthorizationFailed,
    setAuthorizationLoading,
    setSession,
  ]);

  if (!sessionToken) {
    return (
      <Navigate
        replace
        to={sessionExpired ? "/auth/session-expired" : "/auth/login"}
      />
    );
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
