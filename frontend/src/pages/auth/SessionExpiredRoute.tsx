import { AuthLayout } from "./AuthLayout";
import { LoginPanel } from "./LoginPanel";
import { SessionExpiredStatus } from "./SessionExpiredStatus";
import { useLoginFlow } from "./use-login-flow";

export function SessionExpiredRoute() {
  const login = useLoginFlow();
  return (
    <AuthLayout rail={<SessionExpiredStatus />}>
      <LoginPanel
        submitting={login.submitting}
        error={login.error}
        onSubmit={login.submit}
      />
    </AuthLayout>
  );
}
