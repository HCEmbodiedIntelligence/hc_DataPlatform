import { useSearchParams } from "react-router-dom";
import { AuthLayout } from "./AuthLayout";
import {
  EmptyAccountStatus,
  type EmptyAccountIntent,
} from "./EmptyAccountStatus";
import { LoginPanel } from "./LoginPanel";
import { RegistrationSuccessStatus } from "./RegistrationSuccessStatus";
import styles from "./styles.module.css";

const fixtureErrors: Readonly<Record<string, string>> = {
  "401": "用户名或密码不正确。请检查后重试。",
  "422": "登录信息未通过校验。请检查用户名和密码。",
  "429": "尝试次数过多。请稍后再试，平台不会显示账户是否存在。",
};

export function VisualFixtureRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const errorCode = searchParams.get("error");
  const error = errorCode ? (fixtureErrors[errorCode] ?? null) : null;

  function selectIntent(intent: EmptyAccountIntent): void {
    const next = new URLSearchParams(searchParams);
    next.set("intent", intent);
    setSearchParams(next, { replace: true });
  }

  return (
    <AuthLayout
      rail={
        <div className={styles.fixtureRail}>
          <RegistrationSuccessStatus compact />
          <EmptyAccountStatus compact onIntent={selectIntent} />
        </div>
      }
    >
      <LoginPanel submitting={false} error={error} onSubmit={() => undefined} />
    </AuthLayout>
  );
}
