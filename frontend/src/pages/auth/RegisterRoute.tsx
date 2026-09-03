import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";
import { AccessGuideStatus } from "./AccessGuideStatus";
import {
  getPublicAuthConfiguration,
  registerAccount,
  type PasswordPolicyView,
} from "./api";
import { authErrorMessage } from "./error-messages";
import { RegisterPanel, type RegistrationValues } from "./RegisterPanel";

export function RegisterRoute() {
  const navigate = useNavigate();
  const inFlightRef = useRef(false);
  const mountedRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passwordPolicy, setPasswordPolicy] =
    useState<PasswordPolicyView | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    void getPublicAuthConfiguration(controller.signal)
      .then((configuration) => {
        if (!controller.signal.aborted) {
          setPasswordPolicy(configuration.password_policy);
        }
      })
      .catch(() => {
        // Discovery is guidance only. The server remains the policy authority.
      });
    return () => {
      mountedRef.current = false;
      controller.abort();
    };
  }, []);

  async function submit(values: RegistrationValues): Promise<void> {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await registerAccount(values.username, values.password);
      if (!mountedRef.current) return;
      navigate("/auth/registered", {
        replace: true,
        state: { username: result.principal.username },
      });
    } catch (reason) {
      if (mountedRef.current) setError(authErrorMessage(reason, "register"));
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current) setSubmitting(false);
    }
  }

  return (
    <AuthLayout rail={<AccessGuideStatus />}>
      <RegisterPanel
        submitting={submitting}
        error={error}
        passwordPolicy={passwordPolicy}
        onSubmit={submit}
      />
    </AuthLayout>
  );
}
