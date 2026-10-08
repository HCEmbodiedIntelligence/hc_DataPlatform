import { definePage } from "../../shared/routing/page-descriptor";

export const page = definePage({
  id: "ACCOUNT",
  enabled: true,
  routes: [
    {
      path: "/account",
      requiredCapabilities: [],
      lazy: async () => ({ Component: (await import("./page")).default }),
    },
  ],
});
