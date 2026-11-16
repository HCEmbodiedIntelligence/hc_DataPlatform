// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { McapFailureChannels } from "./McapFailureChannels";

it("shows the current file's topic names, message types and exact counts, including zero", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(),
  }) });
  const renderFile = (topic: string, count: number) => (
    <McapFailureChannels inventory={{ [topic]: { schema: "sensor_msgs/msg/Image", message_count: count } }}
      configuration={{}} error={null} />
  );
  const view = render(renderFile("/left", 43));
  expect(within(screen.getByText("/left").closest("tr")!).getByText("43")).toBeTruthy();
  view.rerender(renderFile("/right", 0));
  const row = within(screen.getByText("/right").closest("tr")!);
  expect(row.getByText("0")).toBeTruthy();
  expect(row.getByText("sensor_msgs/msg/Image")).toBeTruthy();
  expect(screen.queryByText("/left")).toBeNull();
});
