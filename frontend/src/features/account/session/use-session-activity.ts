import { useEffect } from "react";
import { isDomainError } from "../../../shared/api/domain-error";
import { useShellStore } from "../../../shared/scope/shell-store";
import { getSessionBootstrap } from "./api";

const KEEPALIVE_INTERVAL_MS = 60_000;
const REQUEST_TIMEOUT_MS = 15_000;
const INPUT_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "wheel",
  "scroll",
  "touchstart",
] as const;

/** Count local editing and foreground playback as session activity. */
export function useSessionActivity(enabled: boolean): void {
  const sessionToken = useShellStore((state) => state.sessionToken);
  const sessionRevision = useShellStore((state) => state.sessionRevision);

  useEffect(() => {
    if (!enabled || !sessionToken) return;
    const token = sessionToken;
    let stopped = false;
    let activityPending = false;
    let lastAttemptAt = Date.now();
    let inFlight: AbortController | null = null;

    function isCurrentSession(): boolean {
      const current = useShellStore.getState();
      return (
        !stopped &&
        current.sessionToken === token &&
        current.sessionRevision === sessionRevision &&
        current.bootstrapLoaded
      );
    }

    async function renewIfNeeded(): Promise<void> {
      if (
        !activityPending ||
        inFlight ||
        document.visibilityState !== "visible" ||
        !isCurrentSession() ||
        Date.now() - lastAttemptAt < KEEPALIVE_INTERVAL_MS
      )
        return;

      activityPending = false;
      lastAttemptAt = Date.now();
      const controller = new AbortController();
      inFlight = controller;
      const timeout = window.setTimeout(
        () => controller.abort(),
        REQUEST_TIMEOUT_MS,
      );
      try {
        // This authenticated endpoint already touches last_seen_at on the server.
        // Bind the request to this login so an old 401 cannot clear a new login.
        const bootstrap = await getSessionBootstrap({
          bearerToken: token,
          signal: controller.signal,
        });
        if (controller.signal.aborted || !isCurrentSession()) return;
        const current = useShellStore.getState();
        if (
          current.capabilityRevision !== null &&
          bootstrap.capability_revision > current.capabilityRevision
        ) {
          current.invalidateAuthorization(
            token,
            sessionRevision,
            current.capabilityRevision,
          );
        }
        // An unchanged bootstrap must not reinstall the scope or reset editors.
      } catch (reason) {
        if (controller.signal.aborted || !isCurrentSession()) return;
        if (isDomainError(reason) && reason.httpStatus === 401) {
          useShellStore.getState().expireSession(token);
        }
        // Offline/5xx failures leave the session intact. Further activity retries.
      } finally {
        window.clearTimeout(timeout);
        inFlight = null;
      }
    }

    function recordActivity(): void {
      if (document.visibilityState !== "visible" || !isCurrentSession()) return;
      activityPending = true;
      // A returning user should not have to wait for the next interval tick.
      void renewIfNeeded();
    }

    function recordPlayback(event: Event): void {
      const media = event.target;
      if (media instanceof HTMLMediaElement && !media.paused && !media.ended) {
        recordActivity();
      }
    }

    function visibilityChanged(): void {
      activityPending = false;
      if (document.visibilityState === "visible") recordActivity();
    }

    const listenerOptions = { capture: true, passive: true } as const;
    for (const event of INPUT_EVENTS) {
      document.addEventListener(event, recordActivity, listenerOptions);
    }
    document.addEventListener("timeupdate", recordPlayback, listenerOptions);
    document.addEventListener("visibilitychange", visibilityChanged);
    window.addEventListener("focus", recordActivity);
    const interval = window.setInterval(
      () => void renewIfNeeded(),
      KEEPALIVE_INTERVAL_MS,
    );
    return () => {
      stopped = true;
      window.clearInterval(interval);
      inFlight?.abort();
      for (const event of INPUT_EVENTS) {
        document.removeEventListener(event, recordActivity, listenerOptions);
      }
      document.removeEventListener(
        "timeupdate",
        recordPlayback,
        listenerOptions,
      );
      document.removeEventListener("visibilitychange", visibilityChanged);
      window.removeEventListener("focus", recordActivity);
    };
  }, [enabled, sessionToken, sessionRevision]);
}
