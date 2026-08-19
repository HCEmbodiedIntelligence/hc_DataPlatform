import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";
import { AccessGuideStatus } from "./AccessGuideStatus";
import { registerAccount } from "./api";
import { authErrorMessage } from "./error-messages";
import { RegisterPanel, type RegistrationValues } from "./RegisterPanel";

export function RegisterRoute() {
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(values: RegistrationValues): Promise<void> {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await registerAccount(values.username, values.password);
      navigate("/auth/registered", {
        replace: true,
        state: { username: result.principal.username },
      });
    } catch (reason) {
      setError(authErrorMessage(reason, "register"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout rail={<AccessGuideStatus />}>
      <RegisterPanel submitting={submitting} error={error} onSubmit={submit} />
    </AuthLayout>
  );
}
