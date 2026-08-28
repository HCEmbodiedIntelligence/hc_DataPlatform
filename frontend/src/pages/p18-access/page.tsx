import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { expandGrantedCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState } from "../../shared/ui";
import { AccessApprovalView } from "./AccessApprovalView";
import { UserManagementContainer } from "./UserManagementContainer";
import {
  useAccessDecision,
  useCapabilityRequests,
  useMembershipRequests,
  type AccessDecisionInput,
} from "./access-api";
import { capabilityRequestRow, membershipRequestRow } from "./contracts";
import { decisionLabel } from "./presentation";
import { accessQueryCodec, type AccessSearch } from "./query-codec";

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = accessQueryCodec.parse(params);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const activeScope = useShellStore((state) => state.scope);
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  const authorization = useShellStore((state) => state.authorization);
  const capabilityKeys = authorization?.capabilities as
    | readonly string[]
    | undefined;
  const canManage =
    capabilityKeys?.some(
      (key) => key === "project.access.manage" || key === "access.manage",
    ) ?? false;
  const projectCapabilities = expandGrantedCapabilities(capabilityKeys ?? []);
  const platformCapabilities = useShellStore(
    (state) => state.platformCapabilities,
  );
  const canReadPlatformAccounts = platformCapabilities.some(
    (key) =>
      key === "platform.account.read" || key === "platform.account.manage",
  );
  const canManagePlatformAccounts = platformCapabilities.includes(
    "platform.account.manage",
  );
  const canUnlockPlatformAccounts = platformCapabilities.includes(
    "platform.account_security.manage",
  );
  const canReadProjectRequests =
    activeScope !== null && projectCapabilities.has("access.read");
  const platformOnlyAccount = activeScope === null && canReadPlatformAccounts;
  const effectiveSearch: AccessSearch = platformOnlyAccount
    ? {
        ...search,
        tab: "users",
        q: search.tab === "users" ? search.q : undefined,
        page: search.tab === "users" ? search.page : 1,
        requestId: undefined,
        drawer: "closed",
      }
    : search;
  const membershipQuery = useMembershipRequests(
    canReadProjectRequests && effectiveSearch.tab === "membership-requests",
  );
  const capabilityQuery = useCapabilityRequests(
    canReadProjectRequests && effectiveSearch.tab === "capability-requests",
  );
  const decision = useAccessDecision();

  const membershipRows = useMemo(
    () => membershipQuery.data?.items.map(membershipRequestRow) ?? [],
    [membershipQuery.data?.items],
  );
  const capabilityRows = useMemo(
    () => capabilityQuery.data?.items.map(capabilityRequestRow) ?? [],
    [capabilityQuery.data?.items],
  );

  const changeSearch = (patch: Partial<AccessSearch>) => {
    const contextChanged = Object.keys(patch).some(
      (key) => key !== "drawer" && key !== "requestId",
    );
    if (contextChanged || patch.drawer === "open") decision.reset();
    const nextSearch = {
      ...effectiveSearch,
      ...patch,
      ...(contextChanged || patch.drawer === "closed"
        ? { requestId: undefined, drawer: "closed" as const }
        : {}),
    };
    setParams(accessQueryCodec.build(nextSearch), { replace: true });
  };

  const submitDecision = (input: AccessDecisionInput) => {
    decision.reset();
    decision.mutate(input, {
      onSuccess: () => {
        setParams(
          accessQueryCodec.build({
            ...search,
            requestId: undefined,
            drawer: "closed",
          }),
          { replace: true },
        );
      },
    });
  };

  const previousScopeKeyRef = useRef(scopeKey);
  useEffect(() => {
    const scopeChanged = previousScopeKeyRef.current !== scopeKey;
    previousScopeKeyRef.current = scopeKey;
    decision.reset();
    if (!scopeChanged) return;
    setParams(
      (currentParams) =>
        accessQueryCodec.build({
          ...accessQueryCodec.parse(currentParams),
          requestId: undefined,
          drawer: "closed",
        }),
      { replace: true },
    );
  }, [decision.reset, scopeKey, setParams]);

  useEffect(() => {
    if (!platformOnlyAccount || search.tab === "users") return;
    setParams(accessQueryCodec.build(effectiveSearch), { replace: true });
  }, [effectiveSearch, platformOnlyAccount, search.tab, setParams]);

  const decisionSuccessMessage =
    decision.isSuccess && decision.variables
      ? `${decisionLabel(decision.variables.action)}已由服务端确认。`
      : undefined;

  if (!canReadPlatformAccounts && !canReadProjectRequests) {
    return (
      <PageState
        state="forbidden"
        label="账户与权限"
        description="当前身份没有项目审批读取权限或平台账号读取权限。"
      />
    );
  }

  return (
    <AccessApprovalView
      search={effectiveSearch}
      membership={{
        rows: membershipRows,
        loading: membershipQuery.isPending,
        fetching: membershipQuery.isFetching,
        error: membershipQuery.error,
      }}
      capability={{
        rows: capabilityRows,
        loading: capabilityQuery.isPending,
        fetching: capabilityQuery.isFetching,
        error: capabilityQuery.error,
      }}
      canManage={canManage}
      canReadPlatformAccounts={canReadPlatformAccounts}
      canReadProjectRequests={canReadProjectRequests}
      userManagement={
        <UserManagementContainer
          search={effectiveSearch}
          enabled={effectiveSearch.tab === "users" && canReadPlatformAccounts}
          canManage={canManagePlatformAccounts}
          canUnlock={canUnlockPlatformAccounts}
          onSearchChange={changeSearch}
        />
      }
      principalId={principalId}
      decisionPending={decision.isPending}
      decisionError={decision.isSuccess ? null : decision.error}
      decisionRequestId={decision.variables?.requestId}
      decisionSuccessMessage={decisionSuccessMessage}
      onDecisionSuccessDismiss={decision.reset}
      onSearchChange={changeSearch}
      onRefresh={(tab) => {
        decision.reset();
        if (tab === "membership-requests") void membershipQuery.refetch();
        else void capabilityQuery.refetch();
      }}
      onDecision={submitDecision}
    />
  );
}

export default Component;
