import type { RouteObject } from "react-router-dom";

export const authRoutes: RouteObject[] = [
  {
    path: "/auth/login",
    lazy: async () => ({
      Component: (await import("./LoginRoute")).LoginRoute,
    }),
  },
  {
    path: "/auth/register",
    lazy: async () => ({
      Component: (await import("./RegisterRoute")).RegisterRoute,
    }),
  },
  {
    path: "/auth/recover-password",
    lazy: async () => ({
      Component: (await import("./PasswordRecoveryRoutes"))
        .PasswordRecoveryRequestRoute,
    }),
  },
  {
    path: "/auth/reset-password",
    lazy: async () => ({
      Component: (await import("./PasswordRecoveryRoutes")).PasswordResetRoute,
    }),
  },
  {
    path: "/auth/registered",
    lazy: async () => ({
      Component: (await import("./RegisteredRoute")).RegisteredRoute,
    }),
  },
  {
    path: "/auth/session-expired",
    lazy: async () => ({
      Component: (await import("./SessionExpiredRoute")).SessionExpiredRoute,
    }),
  },
];

if (
  import.meta.env.DEV &&
  import.meta.env.VITE_AUTH_VISUAL_FIXTURE === "true"
) {
  authRoutes.push({
    path: "/__visual__/e01",
    lazy: async () => ({
      Component: (await import("./VisualFixtureRoute")).VisualFixtureRoute,
    }),
  });
}
