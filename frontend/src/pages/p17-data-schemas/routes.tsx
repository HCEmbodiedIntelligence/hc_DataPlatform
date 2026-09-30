import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
// Schema authoring is intentionally unavailable; contracts belong to configuration packages.

export const routes: PageRoute[] = [];

export const page = definePage({ id: "P17", enabled: false, routes: routes });
export default routes;
