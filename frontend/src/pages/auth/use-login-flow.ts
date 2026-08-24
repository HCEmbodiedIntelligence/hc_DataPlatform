import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  createDomainError,
  isDomainError,
} from "../../shared/api/domain-error";
import { getShellState, useShellStore } from "../../shared/scope/shell-store";
import {
  createSession,
  getSessionBootstrap,
  logoutSession,
  toActorSummary,
} from "./api";
import { authErrorMessage } from "./error-messages";
import { installSessionBootstrap } from "./runtime-scope";

export interface LoginValues {
  readonly username: string;
  readonly password: string;
}

function discardIssuedSession(token: string): void {
  void logoutSession({ bearerToken: token }).catch(() => {
    // Best effort only: never let cleanup replace the active login result.
  });
}

function sessionContractMismatch(): Error {
  return createDomainError({
    code: "CONTRACT_MISMATCH",
    message: "服务端响应与当前认证合同不匹配",
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

export function useLoginFlow() {
  const navigate = useNavigate();
  const setSession = useShellStore((state) => state.setSession);
  const inFlightRef = useRef(false);
  const mountedRef = useRef(false);
  const attemptRef = useRef(0);
  const bootstrapRef = useRef<{
    readonly attempt: number;
    readonly controller: AbortController;
  } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [challengeRequired, setChallengeRequired] = useState(false);
  const [challengeResetKey, setChallengeResetKey] = useState(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      attemptRef.current += 1;
      bootstrapRef.current?.controller.abort();
      bootstrapRef.current = null;
    };
  }, []);

  async function submit(
    values: LoginValues,
    challengeResponse?: string,
  ): Promise<void> {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    const attempt = ++attemptRef.current;
    const isCurrentAttempt = () =>
      mountedRef.current && attemptRef.current === attempt;
    setSubmitting(true);
    setError(null);
    let phase: "login" | "bootstrap" = "login";
    let issuedToken: string | null = null;
    try {
      const session = await createSession(values.username, values.password, {
        ...(challengeResponse === undefined ? {} : { challengeResponse }),
        onIssuedToken: (token) => {
          issuedToken = token;
        },
      });
      const token = session.access_token;
      issuedToken = token;
      if (!isCurrentAttempt()) {
        discardIssuedSession(token);
        issuedToken = null;
        return;
      }
      phase = "bootstrap";
      const controller = new AbortController();
      bootstrapRef.current = { attempt, controller };
      const bootstrap = await getSessionBootstrap({
        bearerToken: token,
        signal: controller.signal,
      });
      if (!isCurrentAttempt()) {
        discardIssuedSession(token);
        issuedToken = null;
        return;
      }
      if (bootstrap.principal.principal_id !== session.principal.principal_id) {
        throw sessionContractMismatch();
      }
      setSession(toActorSummary(bootstrap.principal), token);
      installSessionBootstrap(bootstrap);
      issuedToken = null;
      const canManagePlatformAccounts =
        bootstrap.platform_capabilities?.includes("platform.admin") ||
        bootstrap.platform_capabilities?.includes("platform.account.read") ||
        bootstrap.platform_capabilities?.includes("platform.account.manage");
      navigate(
        bootstrap.available_scopes.length === 0
          ? canManagePlatformAccounts
            ? "/settings/access?tab=users"
            : "/account/empty"
          : "/",
        {
          replace: true,
        },
      );
    } catch (reason) {
      if (issuedToken !== null) {
        const discardedToken = issuedToken;
        issuedToken = null;
        discardIssuedSession(discardedToken);
        const shell = getShellState();
        if (shell.sessionToken === discardedToken) shell.setSession(null, null);
      }
      if (!isCurrentAttempt()) return;
      const challengeRequested =
        isDomainError(reason) &&
        (reason.problemCode === "AUTH_CHALLENGE_REQUIRED" ||
          reason.problemCode === "AUTH_CHALLENGE_INVALID");
      if (challengeRequested) setChallengeRequired(true);
      if (
        phase === "bootstrap" &&
        isDomainError(reason) &&
        reason.httpStatus === 401
      ) {
        navigate("/auth/session-expired", { replace: true });
        return;
      }
      setError(authErrorMessage(reason, phase));
    } finally {
      if (bootstrapRef.current?.attempt === attempt)
        bootstrapRef.current = null;
      if (attemptRef.current === attempt) inFlightRef.current = false;
      if (isCurrentAttempt()) {
        if (challengeResponse !== undefined) {
          setChallengeResetKey((value) => value + 1);
        }
        setSubmitting(false);
      }
    }
  }

  return {
    submitting,
    error,
    challengeRequired,
    challengeResetKey,
    submit,
  } as const;
}
