import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useShellStore } from "../../shared/scope/shell-store";
import { AccessApprovalView } from "./AccessApprovalView";
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
  const membershipQuery = useMembershipRequests();
  const capabilityQuery = useCapabilityRequests();
  const decision = useAccessDecision();
  const scopeKey = useShellStore((state) => state.scopeKey);
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

  const membershipRows = useMemo(
    () => membershipQuery.data?.items.map(membershipRequestRow) ?? [],
    [membershipQuery.data?.items],
  );
  const capabilityRows = useMemo(
    () => capabilityQuery.data?.items.map(capabilityRequestRow) ?? [],
    [capabilityQuery.data?.items],
  );

  const changeSearch = (patch: Partial<AccessSearch>) => {
    if (Object.keys(patch).some((key) => key !== "drawer")) decision.reset();
    setParams(accessQueryCodec.build({ ...search, ...patch }), {
      replace: true,
    });
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

  useEffect(() => {
    decision.reset();
  }, [decision.reset, scopeKey]);

  const decisionSuccessMessage =
    decision.isSuccess && decision.variables
      ? `${decisionLabel(decision.variables.action)}已由服务端确认。`
      : undefined;

  return (
    <AccessApprovalView
      search={search}
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
      principalId={principalId}
      decisionPending={decision.isPending}
      decisionError={decision.isSuccess ? null : decision.error}
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
