// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import type { ActorSummary } from "../../entities/actor";
import type { Scope } from "../../entities/scope";
import { readPersistedSession, writePersistedSession } from "./session-storage";

const principal: ActorSummary = {
  actorId: "principal-admin",
  displayName: "hc-admin",
  roleIds: [],
};

const scope: Scope = {
  organizationId: "organization-real",
  projectId: "project-real",
  regionCode: "cn-real",
};

afterEach(() => sessionStorage.clear());

describe("current-tab session storage", () => {
  it("round-trips only the session identity, opaque token, and scope", () => {
    writePersistedSession(principal, "opaque-session-token", scope);

    expect(readPersistedSession()).toEqual({
      principal,
      sessionToken: "opaque-session-token",
      scope,
    });
    expect(sessionStorage.getItem("hc-platform-current-session")).not.toContain(
      "capabilities",
    );
  });

  it("clears the persisted session on logout", () => {
    writePersistedSession(principal, "opaque-session-token", scope);

    writePersistedSession(null, null, null);

    expect(readPersistedSession()).toBeNull();
  });

  it("fails closed and removes malformed persisted state", () => {
    sessionStorage.setItem("hc-platform-current-session", "not-json");

    expect(readPersistedSession()).toBeNull();
    expect(sessionStorage.getItem("hc-platform-current-session")).toBeNull();
  });
});
