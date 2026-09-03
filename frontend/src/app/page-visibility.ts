const hiddenPageIds: ReadonlySet<string> = new Set(["P16"]);

export function isPageHidden(pageId: string): boolean {
  return hiddenPageIds.has(pageId);
}
