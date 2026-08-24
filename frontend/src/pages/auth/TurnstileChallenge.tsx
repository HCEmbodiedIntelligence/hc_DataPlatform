import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import type { PublicAuthChallengeConfiguration } from "./api";
import styles from "./styles.module.css";

const TURNSTILE_SCRIPT_ID = "hc-turnstile-api";
const TURNSTILE_SCRIPT_URL =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileOptions {
  readonly sitekey: string;
  readonly action: "login" | "password_recovery";
  readonly responseField: false;
  readonly callback: (token: string) => void;
  readonly "expired-callback": () => void;
  readonly "error-callback": () => void;
}

interface TurnstileApi {
  render(container: HTMLElement, options: TurnstileOptions): string;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let scriptPromise: Promise<TurnstileApi> | null = null;

function loadTurnstileScript(): Promise<TurnstileApi> {
  if (window.turnstile !== undefined) return Promise.resolve(window.turnstile);
  if (
    scriptPromise !== null &&
    document.getElementById(TURNSTILE_SCRIPT_ID) === null
  ) {
    scriptPromise = null;
  }
  if (scriptPromise !== null) return scriptPromise;

  scriptPromise = new Promise<TurnstileApi>((resolve, reject) => {
    const existing = document.getElementById(TURNSTILE_SCRIPT_ID);
    const script =
      existing instanceof HTMLScriptElement
        ? existing
        : document.createElement("script");
    script.id = TURNSTILE_SCRIPT_ID;
    script.src = TURNSTILE_SCRIPT_URL;
    script.async = true;
    script.defer = true;
    script.addEventListener(
      "load",
      () => {
        if (window.turnstile === undefined) {
          reject(new Error("Turnstile script loaded without its browser API"));
          return;
        }
        resolve(window.turnstile);
      },
      { once: true },
    );
    script.addEventListener(
      "error",
      () => reject(new Error("Turnstile script failed to load")),
      { once: true },
    );
    if (existing === null) document.head.append(script);
  }).catch((reason: unknown) => {
    document.getElementById(TURNSTILE_SCRIPT_ID)?.remove();
    scriptPromise = null;
    throw reason;
  });
  return scriptPromise;
}

interface TurnstileChallengeProps {
  readonly configuration: PublicAuthChallengeConfiguration;
  readonly action?: "login" | "password_recovery";
  readonly resetKey: number;
  readonly onToken: (token: string | null) => void;
}

export function TurnstileChallenge({
  configuration,
  action = "login",
  resetKey,
  onToken,
}: TurnstileChallengeProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<TurnstileApi | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  const [loadFailed, setLoadFailed] = useState(false);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    onTokenRef.current = onToken;
  }, [onToken]);

  useEffect(() => {
    let disposed = false;
    const container = containerRef.current;
    if (container === null) return undefined;
    setLoadFailed(false);
    onTokenRef.current(null);
    void loadTurnstileScript()
      .then((api) => {
        if (disposed) return;
        apiRef.current = api;
        widgetIdRef.current = api.render(container, {
          sitekey: configuration.site_key,
          action,
          responseField: false,
          callback: (token) => {
            if (!disposed) onTokenRef.current(token);
          },
          "expired-callback": () => {
            if (!disposed) onTokenRef.current(null);
          },
          "error-callback": () => {
            if (disposed) return;
            onTokenRef.current(null);
            setLoadFailed(true);
          },
        });
      })
      .catch(() => {
        if (!disposed) setLoadFailed(true);
      });
    return () => {
      disposed = true;
      const api = apiRef.current;
      const widgetId = widgetIdRef.current;
      apiRef.current = null;
      widgetIdRef.current = null;
      if (api !== null && widgetId !== null) api.remove(widgetId);
    };
  }, [action, configuration.site_key, generation]);

  useEffect(() => {
    const api = apiRef.current;
    const widgetId = widgetIdRef.current;
    if (api === null || widgetId === null) return;
    onToken(null);
    api.reset(widgetId);
  }, [onToken, resetKey]);

  return (
    <section
      className={styles.challengeSection}
      aria-labelledby="auth-challenge-title"
    >
      <h2 id="auth-challenge-title">安全验证</h2>
      <p>
        请完成验证后继续
        {action === "password_recovery" ? "找回密码" : "登录"}
        。验证结果只会发送给认证服务。
      </p>
      {loadFailed ? (
        <Alert
          type="error"
          showIcon
          title="无法加载安全验证"
          description="请检查网络后重新加载验证组件。"
          action={
            <Button
              size="small"
              onClick={() => setGeneration((value) => value + 1)}
            >
              重新加载
            </Button>
          }
        />
      ) : null}
      <div
        ref={containerRef}
        className={styles.challengeWidget}
        aria-live="polite"
      />
    </section>
  );
}
