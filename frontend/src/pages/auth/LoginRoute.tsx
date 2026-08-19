import { AuthLayout } from "./AuthLayout";
import { AccessGuideStatus } from "./AccessGuideStatus";
import { LoginPanel } from "./LoginPanel";
import { useLoginFlow } from "./use-login-flow";

export function LoginRoute() {
  const login = useLoginFlow();
  return (
    <AuthLayout rail={<AccessGuideStatus />}>
      <LoginPanel
        submitting={login.submitting}
        error={login.error}
        onSubmit={login.submit}
      />
    </AuthLayout>
  );
}
