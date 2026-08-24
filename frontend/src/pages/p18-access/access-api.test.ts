import { beforeEach, describe, expect, it, vi } from "vitest";
import { isDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import type { CapabilityRequest, MembershipRequest } from "./contracts";
import {
  decideAccessRequest,
  listCapabilityRequests,
  listMembershipRequests,
} from "./access-api";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "organization-a",
  projectId: "project-a",
  regionCode: "cn-test",
} as const;
const membership: MembershipRequest = {
  request_id: "membership-1",
  organization_id: scope.organizationId,
  project_id: "project-a",
  requester_id: "user-a",
  status: "PENDING",
  reason: "加入项目",
  created_at: "2026-08-18T01:00:00Z",
  updated_at: "2026-08-18T01:00:00Z",
  revision: 1,
};
const capability: CapabilityRequest = {
  ...membership,
  request_id: "capability-1",
  capability_keys: ["datasets.read"],
};

beforeEach(() => {
  requestMock.mockReset();
});

describe("P18 formal runtime access gateway", () => {
  it("loads only the active project membership and capability collections without mock fallback", async () => {
    requestMock
      .mockResolvedValueOnce({ items: [membership] })
      .mockResolvedValueOnce({ items: [capability] });

    await expect(listMembershipRequests(scope)).resolves.toEqual({
      items: [membership],
    });
    await expect(listCapabilityRequests(scope)).resolves.toEqual({
      items: [capability],
    });

    expect(requestMock).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/organizations/organization-a/projects/project-a/membership-requests",
      scope,
      cache: "no-store",
    });
    expect(requestMock).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/organizations/organization-a/projects/project-a/capability-requests",
      scope,
      cache: "no-store",
    });
  });

  it("fails closed when a list leaks a different project scope", async () => {
    requestMock.mockResolvedValue({
      items: [{ ...membership, project_id: "project-b" }],
    });

    const error = await listMembershipRequests(scope).catch(
      (caught: unknown) => caught,
    );
    expect(isDomainError(error)).toBe(true);
    expect(error).toMatchObject({
      code: "CONTRACT_MISMATCH",
      message: expect.stringContaining("当前项目之外"),
    });
  });

  it.each([
    ["membership", "approve", "membership-requests", "membership-1", null],
    ["membership", "reject", "membership-requests", "membership-1", "资料不足"],
    [
      "membership",
      "withdraw",
      "membership-requests",
      "membership-1",
      "申请人撤回",
    ],
    ["capability", "revoke", "capability-requests", "capability-1", "轮岗撤权"],
  ] as const)(
    "posts %s %s to the formal action endpoint",
    async (kind, action, resource, requestId, reason) => {
      const response =
        kind === "membership"
          ? { ...membership, request_id: requestId }
          : { ...capability, request_id: requestId };
      requestMock.mockResolvedValue(response);

      await decideAccessRequest(
        {
          kind,
          action,
          projectId: "project-a",
          requestId,
          reason,
        },
        scope,
      );

      expect(requestMock).toHaveBeenCalledWith({
        method: "POST",
        path: `/organizations/organization-a/projects/project-a/${resource}/${requestId}:${action}`,
        body: { reason },
        scope,
        idempotencyKey: expect.any(String),
        cache: "no-store",
      });
    },
  );

  it("rejects a mismatched decision response instead of accepting a repeated or stale projection", async () => {
    requestMock.mockResolvedValue({
      ...membership,
      request_id: "different-request",
    });

    await expect(
      decideAccessRequest(
        {
          kind: "membership",
          action: "approve",
          projectId: "project-a",
          requestId: "membership-1",
          reason: null,
        },
        scope,
      ),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  });
});
