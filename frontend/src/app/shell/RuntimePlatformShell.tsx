import { useEffect } from "react";
import { getSessionBootstrap } from "../../pages/auth/api";
import {
  installSessionBootstrap,
  loadRuntimeAuthorization,
} from "../../pages/auth/runtime-scope";
import { useShellStore } from "../../shared/scope/shell-store";
import type { PageAvailability } from "./navigation-manifest";
import { PlatformShell, type ScopeOption } from "./PlatformShell";

export function RuntimePlatformShell({
  pageAvailability,
}: {
  readonly pageAvailability: PageAvailability;
}) {
  const sessionToken = useShellStore((state) => state.sessionToken);
  const sessionScopes = useShellStore((state) => state.sessionScopes);
  const setAuthorizationLoading = useShellStore(
    (state) => state.setAuthorizationLoading,
  );
  const setAuthorizationFailed = useShellStore(
    (state) => state.setAuthorizationFailed,
  );

  useEffect(() => {
    if (!sessionToken || sessionScopes.length > 0) return;
    const controller = new AbortController();
    setAuthorizationLoading();
    void getSessionBootstrap(controller.signal)
      .then(installSessionBootstrap)
      .catch(() => {
        if (!controller.signal.aborted) setAuthorizationFailed();
      });
    return () => controller.abort();
  }, [
    sessionScopes.length,
    sessionToken,
    setAuthorizationFailed,
    setAuthorizationLoading,
  ]);

  const scopeOptions: readonly ScopeOption[] = sessionScopes.flatMap((grant) => {
    const common = {
      organizationId: "",
      organizationName: "当前会话",
      projectId: grant.projectId,
      projectName: grant.projectId,
      projectWide: grant.projectWide,
    } as const;
    return grant.regionCodes.length > 0
      ? grant.regionCodes.map((regionCode) => ({
          ...common,
          regionCode,
          regionName: regionCode,
        }))
      : [common];
  });

  return (
    <PlatformShell
      authorizationLoader={loadRuntimeAuthorization}
      pageAvailability={pageAvailability}
      scopeOptions={scopeOptions}
    />
  );
}
