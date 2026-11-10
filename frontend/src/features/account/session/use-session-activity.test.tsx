// @vitest-environment jsdom

import { act, cleanup, fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDomainError } from "../../../shared/api/domain-error";
import { makeScopeKey } from "../../../shared/scope/model";
import { useShellStore } from "../../../shared/scope/shell-store";
import * as api from "./api";
import { useSessionActivity } from "./use-session-activity";

const MINUTE = 60_000;
const token = "hcs_activity-test";
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "global",
};
const bootstrap: api.SessionBootstrap = {
  principal: {
    principal_id: "activity-user",
    username: "activity-user",
    display_name: "Activity User",
    status: "ACTIVE",
    created_at: "2026-10-28T00:00:00Z",
  },
  available_scopes: [],
  platform_capabilities: [],
  capability_revision: 1,
};

function login(value = token): void {
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "activity-user", displayName: "Activity User", roleIds: [] },
    value,
  );
  store.setScope(scope);
  store.setSessionScopes([], 1);
  store.setAuthorization({
    scopeKey: makeScopeKey(scope),
    roleVersion: "1",
    capabilities: ["episode.read"],
    fetchedAt: new Date().toISOString(),
  });
}

function unauthorized() {
  return createDomainError({
    code: "UNAUTHENTICATED",
    message: "Session expired",
    httpStatus: 401,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
  });
}

async function advance(milliseconds = MINUTE): Promise<void> {
  await act(async () => vi.advanceTimersByTimeAsync(milliseconds));
}

describe("session activity renewal", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-28T00:00:00Z"));
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.spyOn(api, "getSessionBootstrap").mockResolvedValue(bootstrap);
    login();
  });

  afterEach(() => {
    cleanup();
    useShellStore.getState().setSession(null, null);
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renews during local editing without resetting scope, authorization or session identity", async () => {
    renderHook(() => useSessionActivity(true));
    const before = useShellStore.getState();
    for (let minute = 0; minute < 35; minute += 1) {
      fireEvent.keyDown(document, { key: "ArrowRight" });
      fireEvent.pointerMove(document);
      fireEvent.scroll(document);
      await advance();
    }
    expect(api.getSessionBootstrap).toHaveBeenCalledTimes(35);
    expect(api.getSessionBootstrap).toHaveBeenLastCalledWith({
      bearerToken: token,
      signal: expect.any(AbortSignal),
    });
    const after = useShellStore.getState();
    expect(after.sessionRevision).toBe(before.sessionRevision);
    expect(after.authorization).toBe(before.authorization);
    expect(after.scope).toBe(before.scope);
    expect(after.sessionToken).toBe(token);
  });

  it("does not renew idle pages and renews promptly when an idle user returns", async () => {
    renderHook(() => useSessionActivity(true));
    await advance(35 * MINUTE);
    expect(api.getSessionBootstrap).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.pointerDown(document);
    });
    expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
    await advance(10 * MINUTE);
    expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
  });

  it("ignores hidden activity and resumes on returning to the tab", async () => {
    renderHook(() => useSessionActivity(true));
    fireEvent.keyDown(document, { key: "a" });
    const visibility = vi.spyOn(document, "visibilityState", "get");
    visibility.mockReturnValue("hidden");
    fireEvent(document, new Event("visibilitychange"));
    fireEvent.pointerMove(document);
    await advance(35 * MINUTE);
    expect(api.getSessionBootstrap).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    await act(async () => {
      fireEvent(document, new Event("visibilitychange"));
    });
    expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
  });

  it("counts foreground media playback but not paused media or background playback", async () => {
    renderHook(() => useSessionActivity(true));
    const video = document.createElement("video");
    document.body.appendChild(video);
    try {
      const paused = vi.spyOn(video, "paused", "get").mockReturnValue(false);
      fireEvent.timeUpdate(video);
      await advance();
      expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
      paused.mockReturnValue(true);
      fireEvent.timeUpdate(video);
      await advance();
      expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
      paused.mockReturnValue(false);
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      fireEvent.timeUpdate(video);
      await advance();
      expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
    } finally {
      video.remove();
    }
  });

  it("preserves the login on network errors and retries on continued activity", async () => {
    vi.mocked(api.getSessionBootstrap).mockRejectedValueOnce(
      new TypeError("Offline"),
    );
    renderHook(() => useSessionActivity(true));
    fireEvent.keyDown(document);
    await advance();
    expect(useShellStore.getState().sessionToken).toBe(token);
    fireEvent.keyDown(document);
    await advance();
    expect(api.getSessionBootstrap).toHaveBeenCalledTimes(2);
    expect(useShellStore.getState().sessionExpired).toBe(false);
  });

  it("ends the current login when the server confirms expiry", async () => {
    vi.mocked(api.getSessionBootstrap).mockRejectedValueOnce(unauthorized());
    renderHook(() => useSessionActivity(true));
    fireEvent.keyDown(document);
    await advance();
    expect(useShellStore.getState().sessionToken).toBeNull();
    expect(useShellStore.getState().sessionExpired).toBe(true);
    expect(sessionStorage.getItem("hc-platform-current-session")).toBeNull();
  });

  it("aborts the old request and ignores its delayed 401 after a new login", async () => {
    let rejectOld!: (reason: unknown) => void;
    vi.mocked(api.getSessionBootstrap).mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectOld = reject;
        }),
    );
    renderHook(() => useSessionActivity(true));
    fireEvent.keyDown(document);
    await advance();
    const signal = vi.mocked(api.getSessionBootstrap).mock.calls[0]?.[0]
      ?.signal;
    act(() => login("hcs_new-login"));
    expect(signal?.aborted).toBe(true);
    await act(async () => rejectOld(unauthorized()));
    expect(useShellStore.getState().sessionToken).toBe("hcs_new-login");
    expect(useShellStore.getState().sessionExpired).toBe(false);
  });

  it("bounds slow requests without parallel renewals and retries after a timeout", async () => {
    vi.mocked(api.getSessionBootstrap).mockImplementationOnce(
      ({ signal } = {}) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    renderHook(() => useSessionActivity(true));
    fireEvent.keyDown(document);
    await advance();
    for (let index = 0; index < 20; index += 1) fireEvent.pointerMove(document);
    await advance(15_000);
    expect(api.getSessionBootstrap).toHaveBeenCalledOnce();
    expect(useShellStore.getState().sessionToken).toBe(token);
    await advance(45_000);
    expect(api.getSessionBootstrap).toHaveBeenCalledTimes(2);
  });

  it("refreshes authorization only when the server reports a newer capability revision", async () => {
    vi.mocked(api.getSessionBootstrap).mockResolvedValueOnce({
      ...bootstrap,
      capability_revision: 2,
    });
    renderHook(() => useSessionActivity(true));
    fireEvent.keyDown(document);
    await advance();
    expect(useShellStore.getState().sessionToken).toBe(token);
    expect(useShellStore.getState().bootstrapLoaded).toBe(false);
    expect(useShellStore.getState().authorization).toBeNull();
  });

  it("stops renewing while disabled, on logout and after unmount", async () => {
    const { rerender, unmount } = renderHook(
      ({ enabled }) => useSessionActivity(enabled),
      { initialProps: { enabled: true } },
    );
    fireEvent.keyDown(document);
    rerender({ enabled: false });
    fireEvent.keyDown(document);
    await advance();
    expect(api.getSessionBootstrap).not.toHaveBeenCalled();
    rerender({ enabled: true });
    act(() => useShellStore.getState().setSession(null, null));
    fireEvent.keyDown(document);
    await advance();
    expect(api.getSessionBootstrap).not.toHaveBeenCalled();
    act(() => login());
    fireEvent.keyDown(document);
    unmount();
    await advance();
    expect(api.getSessionBootstrap).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
