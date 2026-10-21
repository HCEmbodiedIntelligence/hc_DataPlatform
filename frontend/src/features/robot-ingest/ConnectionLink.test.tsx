// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { getRuntimeConfig } from "../../shared/config/runtime";
import { ConnectionLink } from "./ConnectionLink";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("../../shared/config/runtime", () => ({
  getRuntimeConfig: vi.fn(),
}));
const scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "global",
};
beforeEach(() => {
  vi.mocked(getRuntimeConfig).mockReturnValue({
    apiBaseUrl: "https://platform.example/api/v1",
    sseBaseUrl: "/api/v1/events",
    buildVersion: "test",
    releaseEnv: "test",
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("copies one ten-minute link, reuses it and never stores secrets or rotates credentials", async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  const storage = vi.spyOn(Storage.prototype, "setItem");
  vi.mocked(request).mockResolvedValue({
    ticket: "one-time-ticket",
    expires_at: new Date(Date.now() + 600000).toISOString(),
    expires_in_seconds: 600,
  });
  render(<ConnectionLink scope={scope} identityId="robot-identity" />);
  await user.click(screen.getByRole("button", { name: "复制连接链接" }));
  await waitFor(() =>
    expect(copy).toHaveBeenCalledWith(
      "https://platform.example/#robot-connect=one-time-ticket",
    ),
  );
  expect(screen.getByLabelText("连接链接")).toHaveValue(
    "https://platform.example/#robot-connect=one-time-ticket",
  );
  await user.click(screen.getByRole("button", { name: "已复制连接链接" }));
  expect(request).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({
      method: "POST",
      scope,
      scopeMode: "explicit",
      body: {},
      cache: "no-store",
      path: "/projects/project-a/robot-ingest/identities/robot-identity/connections",
    }),
  );
  expect(storage).not.toHaveBeenCalled();
});

it("does not generate a link for a disabled identity", () => {
  render(<ConnectionLink scope={scope} identityId="robot-identity" disabled />);
  expect(screen.getByRole("button", { name: "复制连接链接" })).toBeDisabled();
  expect(request).not.toHaveBeenCalled();
});

it("copies the configured LAN platform URL even from a localhost browser", async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  vi.mocked(getRuntimeConfig).mockReturnValue({
    ...getRuntimeConfig(),
    apiBaseUrl: "/api/v1",
    robotPlatformBaseUrl: "http://192.168.1.20:8088",
  });
  vi.mocked(request).mockResolvedValue({
    ticket: "one-time-ticket",
    expires_at: new Date(Date.now() + 600000).toISOString(),
    expires_in_seconds: 600,
  });
  render(<ConnectionLink scope={scope} identityId="robot-identity" />);
  await user.click(screen.getByRole("button", { name: "复制连接链接" }));
  await waitFor(() =>
    expect(copy).toHaveBeenCalledWith(
      "http://192.168.1.20:8088/#robot-connect=one-time-ticket",
    ),
  );
});

it("does not issue a ticket when only a loopback platform URL is available", async () => {
  const user = userEvent.setup();
  vi.mocked(getRuntimeConfig).mockReturnValue({
    ...getRuntimeConfig(),
    apiBaseUrl: "http://127.0.0.1:8088/api/v1",
  });
  render(<ConnectionLink scope={scope} identityId="robot-identity" />);
  await user.click(screen.getByRole("button", { name: "复制连接链接" }));
  expect(
    await screen.findByText(/当前平台地址仅能本机访问/),
  ).toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("连接链接")).not.toBeInTheDocument();
});

it("copies on HTTP LAN pages without the Clipboard API", async () => {
  const user = userEvent.setup();
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: undefined,
  });
  const command = vi.fn(() => true);
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: command,
  });
  vi.mocked(request).mockResolvedValue({
    ticket: "one-time-ticket",
    expires_at: new Date(Date.now() + 600000).toISOString(),
    expires_in_seconds: 600,
  });
  try {
    render(<ConnectionLink scope={scope} identityId="robot-identity" />);
    await user.click(screen.getByRole("button", { name: "复制连接链接" }));
    expect(
      await screen.findByRole("button", { name: "已复制连接链接" }),
    ).toBeInTheDocument();
    expect(command).toHaveBeenCalledWith("copy");
    expect(screen.queryByText(/浏览器无法复制/)).not.toBeInTheDocument();
  } finally {
    if (descriptor) Object.defineProperty(navigator, "clipboard", descriptor);
    else Reflect.deleteProperty(navigator, "clipboard");
    Reflect.deleteProperty(document, "execCommand");
  }
});
