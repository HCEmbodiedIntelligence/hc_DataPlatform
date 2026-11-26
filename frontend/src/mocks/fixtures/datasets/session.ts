import { transferableAbortController } from "node:util";
import { vi } from "vitest";
import { useShellStore } from "../../../shared/scope/shell-store";

export function openDatasetTestSession(capabilities: string[], roleVersion: string) {
  // MSW forwards requests to Node fetch, which checks the signal's native brand.
  const native = transferableAbortController();
  vi.stubGlobal("AbortController", native.constructor);
  vi.stubGlobal("AbortSignal", native.signal.constructor);
  const store = useShellStore.getState();
  const scope = { organizationId: "org_fx_01", projectId: "prj_fx_01", regionCode: "cn-shanghai" };
  store.setSession({ actorId: "dataset-reader", displayName: "Reader", roleIds: [] }, "fixture-session");
  store.setScope(scope);
  store.setSessionScopes([{ ...scope, regionCodes: [scope.regionCode], projectWide: false, capabilities }], 1);
  store.setAuthorization({
    scopeKey: useShellStore.getState().scopeKey, roleVersion, capabilities,
    fetchedAt: new Date().toISOString(),
  });
}
