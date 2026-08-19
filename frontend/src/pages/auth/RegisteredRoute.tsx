import { useLocation } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";
import { LoginPanel } from "./LoginPanel";
import { RegistrationSuccessStatus } from "./RegistrationSuccessStatus";
import { useLoginFlow } from "./use-login-flow";

interface RegistrationLocationState {
  readonly username?: unknown;
}

export function RegisteredRoute() {
  const location = useLocation();
  const login = useLoginFlow();
  const state = location.state as RegistrationLocationState | null;
  const username =
    typeof state?.username === "string" ? state.username : undefined;

  return (
    <AuthLayout rail={<RegistrationSuccessStatus />}>
      <LoginPanel
        initialUsername={username}
        submitting={login.submitting}
        error={login.error}
        onSubmit={login.submit}
      />
    </AuthLayout>
  );
}
