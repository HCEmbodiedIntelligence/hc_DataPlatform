// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StorageInventoryFact } from "../../../entities/storage-inventory";
import { int64String } from "../../../shared/lib/bigint-string";
import { StorageObjectDrawer } from "./StorageObjectDrawer";

let compact = false;

const object: StorageInventoryFact = {
  objectId: "object-fe13",
  snapshotId: "snapshot-fe13",
  objectRole: "SOURCE",
  wireObjectRole: "SOURCE",
  displayKey: "raw/project-fe13/recording.mcap",
  masked: true,
  physicalBytes: int64String("1024"),
  storageClass: "STANDARD",
  wireStorageClass: "STANDARD",
  status: "AVAILABLE",
  referenceCount: int64String("1"),
  createdAt: "2026-08-18T00:00:00Z",
  lastAccessedAt: null,
  protectionReasons: ["MANIFEST_REFERENCE"],
  allowedActions: [],
  readOnly: true,
};

function DrawerHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        查看对象
      </button>
      <StorageObjectDrawer
        open={open}
        state="ready"
        object={object}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(max-width: 767px)" && compact,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  compact = false;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("StorageObjectDrawer focus lifecycle", () => {
  it("uses drawer-managed initial focus, closes with Escape and restores the trigger", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness />);
    const trigger = screen.getByRole("button", { name: "查看对象" });

    await user.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "对象详情" });
    const close = screen.getByRole("button", { name: "关闭对象详情" });
    expect(screen.queryByText("只读诊断")).not.toBeInTheDocument();
    expect(screen.getByText(object.displayKey)).toBeVisible();
    expect(trigger).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBeInstanceOf(HTMLInputElement);
    expect(close).not.toHaveAttribute("autofocus");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(dialog).not.toBeVisible());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("does not autofocus a control on compact mobile layouts", async () => {
    compact = true;
    const user = userEvent.setup();
    render(<DrawerHarness />);

    await user.click(screen.getByRole("button", { name: "查看对象" }));
    await screen.findByRole("dialog", { name: "对象详情" });
    expect(
      screen.getByRole("button", { name: "关闭对象详情" }),
    ).not.toHaveAttribute("autofocus");
    expect(document.activeElement).not.toBeInstanceOf(HTMLInputElement);
    expect(document.activeElement).not.toBeInstanceOf(HTMLTextAreaElement);
  });
});
