import { definePage } from "../../shared/routing/page-descriptor";

export const page = definePage({
  id: "ACCOUNT_SETTINGS",
  enabled: true,
  routes: [
    {
      path: "/account/settings",
      requiredCapabilities: [],
      lazy: async () => ({ Component: (await import("./page")).default }),
    },
  ],
});
