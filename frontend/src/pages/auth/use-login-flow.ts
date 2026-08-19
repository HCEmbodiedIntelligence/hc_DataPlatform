import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { createSession, getSessionBootstrap, toActorSummary } from "./api";
import { authErrorMessage } from "./error-messages";
import { installSessionBootstrap } from "./runtime-scope";

export interface LoginValues {
  readonly username: string;
  readonly password: string;
}

export function useLoginFlow() {
  const navigate = useNavigate();
  const setSession = useShellStore((state) => state.setSession);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(values: LoginValues): Promise<void> {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    let sessionEstablished = false;
    try {
      const session = await createSession(values.username, values.password);
      setSession(toActorSummary(session.principal), session.access_token);
      sessionEstablished = true;
      const bootstrap = await getSessionBootstrap();
      installSessionBootstrap(bootstrap);
      navigate(
        bootstrap.available_scopes.length === 0 ? "/account/empty" : "/",
        {
          replace: true,
        },
      );
    } catch (reason) {
      if (sessionEstablished) setSession(null, null);
      if (
        sessionEstablished &&
        isDomainError(reason) &&
        reason.httpStatus === 401
      ) {
        navigate("/auth/session-expired", { replace: true });
        return;
      }
      setError(
        authErrorMessage(reason, sessionEstablished ? "bootstrap" : "login"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return { submitting, error, submit } as const;
}
