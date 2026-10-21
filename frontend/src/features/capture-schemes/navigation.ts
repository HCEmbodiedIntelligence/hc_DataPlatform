import type { Scope } from "../../shared/scope/model";
export function safeReturnTo(value: string | null | undefined): string | null {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /[\\\u0000-\u0020]/u.test(value)
  )
    return null;
  try {
    const decoded = decodeURIComponent(value);
    if (/^[\/]{2}|[\\\u0000-\u001f]/u.test(decoded)) return null;
    const url = new URL(value, "https://capture.invalid");
    if (url.origin !== "https://capture.invalid") return null;
    const validPath =
      /^\/(?:settings\/robot-instances|settings\/capture-schemes(?:\/[A-Za-z0-9._-]+)?|collection-tasks|ingest\/sources|ingest\/uploads(?:\/[A-Za-z0-9._-]+)?)\/?$/u.test(
        url.pathname,
      );
    if (!validPath) return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}
export function schemeLibraryPath(back?: string, id?: string, create = false) {
  const params = new URLSearchParams();
  const safe = safeReturnTo(back);
  if (safe) params.set("returnTo", safe);
  return `/settings/capture-schemes${create ? "/new" : id ? `/${encodeURIComponent(id)}` : ""}${params.size ? `?${params}` : ""}`;
}
export function draftKey(kind: string, scope: Scope) {
  return `capture-flow:${kind}:${scope.organizationId}:${scope.projectId ?? ""}:${scope.regionCode ?? ""}`;
}
export function readDraft<T>(key: string): T | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? "null") as T | null;
  } catch {
    return null;
  }
}
export function saveDraft(key: string, value: unknown) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* In-memory form remains usable without storage. */
  }
}
export function clearDraft(key: string) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* Storage can be disabled. */
  }
}
