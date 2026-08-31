import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AppProviders } from "./app/providers";
import { readAppEnvironment, StartupErrorPage } from "./app/env";
import { configureRuntime } from "./shared/config/runtime";
import { configureReleaseIdentity } from "./shared/config/release-identity";
import "./app/theme/global.css";

async function bootstrap(): Promise<void> {
  const rootElement = document.getElementById("root");
  if (rootElement === null) throw new Error("Missing application root element");
  const root = createRoot(rootElement);
  const environment = readAppEnvironment();
  if (!environment.ok) {
    root.render(
      <StrictMode>
        <StartupErrorPage issues={environment.issues} />
      </StrictMode>,
    );
    return;
  }

  configureRuntime({
    apiBaseUrl: environment.value.apiBaseUrl,
    sseBaseUrl: environment.value.sseBaseUrl,
    buildVersion: environment.value.buildVersion,
    releaseEnv: environment.value.releaseEnv,
  });
  configureReleaseIdentity(environment.value.releaseIdentity);

  if (
    import.meta.env.VITE_MOCK_MODE === "browser" &&
    environment.value.mockMode === "browser"
  ) {
    const { startBrowserMocks } = await import("./mocks");
    await startBrowserMocks();
  }

  const { router } = await import("./app/router");

  root.render(
    <StrictMode>
      <AppProviders router={router} />
    </StrictMode>,
  );
}

void bootstrap();
