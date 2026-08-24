import { useEffect, useState } from "react";
import { AuthLayout } from "./AuthLayout";
import { AccessGuideStatus } from "./AccessGuideStatus";
import { LoginPanel } from "./LoginPanel";
import {
  getPublicAuthConfiguration,
  type PublicAuthChallengeConfiguration,
} from "./api";
import { useLoginFlow } from "./use-login-flow";

export function LoginRoute() {
  const login = useLoginFlow();
  const [challengeResponse, setChallengeResponse] = useState<string | null>(
    null,
  );
  const [challengeConfiguration, setChallengeConfiguration] = useState<
    PublicAuthChallengeConfiguration | null | undefined
  >(undefined);
  const [configurationGeneration, setConfigurationGeneration] = useState(0);

  useEffect(() => {
    if (!login.challengeRequired) return undefined;
    const controller = new AbortController();
    setChallengeConfiguration(undefined);
    void getPublicAuthConfiguration(controller.signal)
      .then((configuration) => {
        if (!controller.signal.aborted)
          setChallengeConfiguration(configuration.challenge);
      })
      .catch(() => {
        if (!controller.signal.aborted) setChallengeConfiguration(null);
      });
    return () => controller.abort();
  }, [configurationGeneration, login.challengeRequired]);

  return (
    <AuthLayout rail={<AccessGuideStatus />}>
      <LoginPanel
        submitting={login.submitting}
        error={login.error}
        challengeRequired={login.challengeRequired}
        challengeConfiguration={challengeConfiguration}
        challengeResponse={challengeResponse}
        challengeResetKey={login.challengeResetKey}
        onChallengeResponse={setChallengeResponse}
        onRetryChallengeConfiguration={() =>
          setConfigurationGeneration((generation) => generation + 1)
        }
        onSubmit={(values) =>
          login.submit(values, challengeResponse ?? undefined)
        }
      />
    </AuthLayout>
  );
}
