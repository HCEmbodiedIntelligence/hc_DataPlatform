// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { useDeleteRobotIdentity } from "./api";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
const mockedRequest = vi.mocked(request);
const scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "global",
};
const key = ["robot-ingest", "org-a", "project-a", "global", "list"];

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(() => useDeleteRobotIdentity(), { wrapper }) };
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("deletes with explicit scope and removes the identity and credentials from all organization caches", async () => {
  mockedRequest.mockResolvedValue(undefined);
  const { client, result } = setup();
  const list = {
    items: [{ ingest_identity_id: "rii-a" }, { ingest_identity_id: "rii-b" }],
  };
  const otherProjectKey = [
    "robot-ingest",
    "org-a",
    "project-b",
    "global",
    "list",
  ];
  const otherOrgKey = ["robot-ingest", "org-b", "project-a", "global", "list"];
  const detailKey = [
    "robot-ingest",
    "org-a",
    "project-a",
    "global",
    "rii-a",
    "detail",
  ];
  const credentialKey = [
    "robot-ingest",
    "org-a",
    "project-b",
    "global",
    "rii-a",
    "credentials",
  ];
  for (const queryKey of [key, otherProjectKey, otherOrgKey])
    client.setQueryData(queryKey, list);
  client.setQueryData(detailKey, { data: list.items[0] });
  client.setQueryData(credentialKey, [{ credential_id: "ric-a" }]);
  await act(async () =>
    result.current.mutateAsync({ scope, identityId: "rii-a" }),
  );

  expect(mockedRequest).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({
      method: "DELETE",
      path: "/projects/project-a/robot-ingest/identities/rii-a",
      scope,
      scopeMode: "explicit",
      cache: "no-store",
    }),
  );
  for (const queryKey of [key, otherProjectKey])
    expect(client.getQueryData(queryKey)).toEqual({ items: [list.items[1]] });
  expect(client.getQueryData(otherOrgKey)).toEqual(list);
  expect(client.getQueryData(detailKey)).toBeUndefined();
  expect(client.getQueryData(credentialKey)).toBeUndefined();
});

it("retains cached identities when deletion fails", async () => {
  mockedRequest.mockRejectedValue(new Error("denied"));
  const { client, result } = setup();
  const list = { items: [{ ingest_identity_id: "rii-a" }] };
  client.setQueryData(key, list);
  await act(async () => {
    await expect(
      result.current.mutateAsync({ scope, identityId: "rii-a" }),
    ).rejects.toThrow("denied");
  });
  expect(client.getQueryData(key)).toEqual(list);
});
