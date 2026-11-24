import { useEffect, useRef, useState } from "react";
import { useShellStore } from "../scope/shell-store";
import { saveFileFromClick, type AuthorizedFile } from "./save-file";

export function useFileDownload(context: string) {
  const active = useRef<AbortController | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    setPending(null);
    setError(null);
    const unsubscribe = useShellStore.subscribe((state, previous) => {
      if (state.sessionRevision !== previous.sessionRevision || state.scopeChanging ||
          state.scopeKey !== previous.scopeKey || state.sessionScopes !== previous.sessionScopes) active.current?.abort();
    });
    return () => {
      unsubscribe();
      active.current?.abort();
      active.current = null;
    };
  }, [context]);
  async function save(name: string, load: (signal: AbortSignal) => Promise<AuthorizedFile | Blob>) {
    if (active.current) return null;
    const controller = new AbortController();
    active.current = controller;
    setPending(name);
    setError(null);
    try {
      return await saveFileFromClick(name, controller.signal, load);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause : new Error("文件保存失败。"));
      return null;
    } finally {
      if (active.current === controller) {
        active.current = null;
        setPending(null);
      }
    }
  }
  return { pending, error, save };
}
