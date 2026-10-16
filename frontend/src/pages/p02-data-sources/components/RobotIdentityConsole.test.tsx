// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RobotIdentity } from "../../../features/robot-ingest/model";
import { RobotIdentityConsole } from "./RobotIdentityConsole";

const {
  issueMutate,
  issueReset,
  revokeMutate,
  stateMutate,
  deleteMutate,
  deletion,
} = vi.hoisted(() => ({
  issueMutate: vi.fn(),
  issueReset: vi.fn(),
  revokeMutate: vi.fn(),
  stateMutate: vi.fn(),
  deleteMutate: vi.fn(),
  deletion: { isPending: false, error: null as unknown },
}));

const identity: RobotIdentity = {
  ingest_identity_id: "rii-a",
  organization_id: "org-a",
  robot_id: "robot-a",
  display_name: "采集机器人 A",
  state: "ENABLED",
  allowed_transports: ["HTTPS"],
  allowed_formats: ["MCAP"],
  upload_policy: {
    code: "STANDARD",
    max_asset_size_bytes: 1024,
    max_batch_size_bytes: 2048,
    max_assets: 16,
    part_authorization_ttl_seconds: 900,
    session_retention_hours: 168,
    require_sha256: true,
    require_crc64: true,
  },
  credential_revision: 1,
  last_authenticated_at: "2026-09-01T01:00:00Z",
  last_seen_at: "2026-09-01T01:00:00Z",
  last_upload_at: "2026-09-01T01:10:00Z",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T01:10:00Z",
};

function mutation(mutate = vi.fn()) {
  return {
    mutate,
    reset: vi.fn(),
    isPending: false,
    error: null,
  };
}

vi.mock("../../../features/robot-ingest/api", () => ({
  useRobotIdentities: () => ({
    data: { items: [identity] },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRobotIdentityDetail: () => ({
    data: { data: identity, credential: null },
  }),
  useRobotCredentialHistory: () => ({
    data: [
      {
        credential_id: "ric-a",
        credential_version: 1,
        state: "ACTIVE",
        token_prefix: "hcri_prefix",
        issued_at: "2026-09-01T00:00:00Z",
        expires_at: null,
        revoked_at: null,
        last_authenticated_at: null,
      },
    ],
    isPending: false,
  }),
  useRobotUploadHistory: () => ({
    data: { items: [] },
    isPending: false,
  }),
  useRobotAttemptHistory: () => ({ data: { items: [] } }),
  useRobotStatistics: () => ({
    data: {
      upload_batch_count: 1,
      committed_raw_count: 1,
      episode_count: 2,
      frame_count: 200,
      sample_count: 120,
      capture_duration_ns: 5_000_000_000,
      raw_bytes: 1024,
      technical_failure_count: 0,
      qc_pass_count: 2,
      qc_risk_count: 0,
      qc_reject_count: 0,
      qualified_rate: 1,
    },
  }),
  useRobotIdentityMutation: (operation: string) =>
    mutation(
      operation === "enable" || operation === "disable" ? stateMutate : vi.fn(),
    ),
  useIssueRobotCredential: () => ({
    mutate: issueMutate,
    reset: issueReset,
    isPending: false,
    error: null,
  }),
  useRevokeRobotCredential: () => mutation(revokeMutate),
  useDeleteRobotIdentity: () => ({ ...mutation(deleteMutate), ...deletion }),
}));

vi.mock("../../../features/robots/api/hooks", () => ({
  useScopedRobots: () => ({
    data: {
      items: [{ id: "robot-b", displayName: "采集机器人 B" }],
    },
    isPending: false,
  }),
}));

beforeEach(() => {
  deletion.isPending = false;
  deletion.error = null;
  deleteMutate.mockReset();
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  issueMutate.mockImplementation(
    (
      _input: unknown,
      options: {
        onSuccess: (value: unknown) => void;
      },
    ) =>
      options.onSuccess({
        identity,
        credential: {
          credential_id: "ric-new",
          credential_version: 2,
          token: "hcri_0123456789abcdef0123456789abcdef.once-only-secret-value",
          token_prefix: "hcri_0123456789abcdef0123456789abcdef.once-onl",
          issued_at: "2026-09-01T02:00:00Z",
          expires_at: null,
        },
      }),
  );
  revokeMutate.mockImplementation(
    (_input: unknown, options: { onSuccess: () => void }) =>
      options.onSuccess(),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderConsole(canManage = true) {
  return render(
    <RobotIdentityConsole
      scope={{
        organizationId: "org-a",
        projectId: "project-a",
        regionCode: "cn-east-1",
      }}
      canRead
      canManage={canManage}
    />,
  );
}

describe("P02 robot credential lifecycle", () => {
  it("requires confirmation before deleting the selected identity and explains retained uploads", async () => {
    const user = userEvent.setup();
    renderConsole();
    await user.click(screen.getByRole("button", { name: "删除" }));
    const dialog = screen.getByRole("dialog", { name: "删除机器人上传身份" });
    await waitFor(() =>
      expect(within(dialog).getByText(/确定删除“采集机器人 A”/)).toBeVisible(),
    );
    expect(
      within(dialog).getByText(/已上传的数据、采集任务和历史记录保留/),
    ).toBeVisible();
    expect(deleteMutate).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));
    expect(deleteMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "删除" }));
    deleteMutate.mockImplementation((_input, options) => options.onSuccess());
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    expect(deleteMutate).toHaveBeenCalledExactlyOnceWith(
      {
        scope: {
          organizationId: "org-a",
          projectId: "project-a",
          regionCode: "cn-east-1",
        },
        identityId: "rii-a",
      },
      expect.any(Object),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "删除机器人上传身份" }),
      ).toBeNull(),
    );
  });

  it("keeps the confirmation open on failure so deletion can be retried", async () => {
    const user = userEvent.setup();
    renderConsole();
    deletion.error = new Error("offline");
    await user.click(screen.getByRole("button", { name: "删除" }));
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    const dialog = screen.getByRole("dialog", { name: "删除机器人上传身份" });
    await waitFor(() =>
      expect(within(dialog).getByText("删除未完成")).toBeVisible(),
    );
    expect(deleteMutate).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));
    expect(deleteMutate).toHaveBeenCalledTimes(2);
  });

  it("hides deletion from accounts without management permission", () => {
    renderConsole(false);
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
  });

  it("blocks repeated confirmation while deletion is pending", async () => {
    const user = userEvent.setup();
    renderConsole();
    deletion.isPending = true;
    await user.click(screen.getByRole("button", { name: "删除" }));
    const dialog = screen.getByRole("dialog", { name: "删除机器人上传身份" });
    expect(
      within(dialog).getByRole("button", { name: /取\s*消/ }),
    ).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: /确认删除/ }));
    expect(deleteMutate).not.toHaveBeenCalled();
  });

  it("issues a credential, reveals it once, and clears plaintext on close", async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(screen.getByRole("button", { name: "签发" }));
    await user.click(screen.getByRole("button", { name: "签发凭据" }));

    const token =
      "hcri_0123456789abcdef0123456789abcdef.once-only-secret-value";
    expect(screen.getByDisplayValue(token)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "我已安全保存" }));

    await waitFor(() =>
      expect(screen.queryByDisplayValue(token)).not.toBeInTheDocument(),
    );
    expect(issueReset).toHaveBeenCalled();
  });

  it("rotates with old-credential revocation and can revoke an active credential", async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(screen.getByRole("button", { name: "轮换" }));
    await user.click(screen.getByRole("button", { name: "轮换并撤销旧凭据" }));
    expect(issueMutate).toHaveBeenCalledWith(
      expect.objectContaining({ rotate: true, identityId: "rii-a" }),
      expect.any(Object),
    );
    await user.click(screen.getByRole("button", { name: "我已安全保存" }));

    await user.click(screen.getByRole("button", { name: "撤销" }));
    await user.click(screen.getByRole("button", { name: "确认撤销" }));
    expect(revokeMutate).toHaveBeenCalledWith(
      expect.objectContaining({ credentialId: "ric-a", identityId: "rii-a" }),
      expect.any(Object),
    );
  });
});
