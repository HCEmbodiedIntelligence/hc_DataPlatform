import { pageCatalog } from "./router/page-catalog";

export function isPageHidden(pageId: string): boolean {
  return pageCatalog.availability[pageId] !== true;
}
