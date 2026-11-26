// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { afterEach, expect, it, vi } from "vitest";
import { TimeRangeFilter } from "./TimeRangeFilter";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(start = "2026-11-23T08:50", end = "2026-11-24T08:50") {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const onChange = vi.fn();
  const range = {
    from: new Date(start).toISOString(),
    to: new Date(end).toISOString(),
  };
  render(
    <ConfigProvider locale={zhCN}>
      <TimeRangeFilter
        label="统计时间段"
        range={range}
        onChange={onChange}
        maxDays={31}
        required
      />
    </ConfigProvider>,
  );
  return onChange;
}

function openField(label: string) {
  const input = screen.getByPlaceholderText(label);
  input.focus();
  fireEvent.click(input);
  return input;
}

function dayCell(date: string) {
  return screen.getByTitle(date);
}

function confirm() {
  fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
}

it("keeps the end field stable on hover and blocks days before the existing start", async () => {
  const onChange = setup();
  const end = openField("结束时间");
  const earlier = await screen.findByTitle("2026-11-09");
  expect(earlier).toHaveClass("ant-picker-cell-disabled");
  fireEvent.mouseEnter(earlier);
  fireEvent.click(earlier);
  expect(end).toHaveValue("2026-11-24 08:50");
  fireEvent.mouseEnter(dayCell("2026-11-27"));
  expect(end).toHaveValue("2026-11-24 08:50");
  expect(screen.getByText("一", { selector: "th" })).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
});

it("uses the newly confirmed start through rerenders and submits the selected interval", async () => {
  const onChange = setup();
  openField("开始时间");
  fireEvent.click(await screen.findByTitle("2026-11-09"));
  confirm();
  const end = screen.getByPlaceholderText("结束时间");
  await waitFor(() => expect(end).toHaveFocus());
  expect(dayCell("2026-11-08")).toHaveClass("ant-picker-cell-disabled");
  expect(dayCell("2026-11-10")).not.toHaveClass("ant-picker-cell-disabled");
  fireEvent.click(dayCell("2026-11-10"));
  confirm();
  await waitFor(() =>
    expect(onChange).toHaveBeenCalledWith({
      from: new Date("2026-11-09T08:50").toISOString(),
      to: new Date("2026-11-10T08:50").toISOString(),
    }),
  );
});

it("limits same-day end times to later minutes", async () => {
  setup();
  openField("结束时间");
  fireEvent.click(await screen.findByTitle("2026-11-23"));
  const columns = document.querySelectorAll(".ant-picker-time-panel-column");
  const minute = (text: string) =>
    Array.from(columns[1]!.querySelectorAll("li")).find(
      (cell) => cell.textContent === text,
    );
  expect(minute("50")).toHaveClass("ant-picker-time-panel-cell-disabled");
  expect(minute("51")).not.toHaveClass("ant-picker-time-panel-cell-disabled");
});

it("accepts the first minute of the next day after a 23:59 start", async () => {
  const onChange = setup("2026-11-23T23:59", "2026-11-25T00:00");
  openField("开始时间");
  await screen.findByTitle("2026-11-23");
  confirm();
  await waitFor(() =>
    expect(screen.getByPlaceholderText("结束时间")).toHaveFocus(),
  );
  fireEvent.click(dayCell("2026-11-24"));
  confirm();
  await waitFor(() =>
    expect(onChange).toHaveBeenCalledWith({
      from: new Date("2026-11-23T23:59").toISOString(),
      to: new Date("2026-11-24T00:00").toISOString(),
    }),
  );
});

it("replaces an unfinished edit with a whole preset range", async () => {
  const onChange = setup();
  openField("开始时间");
  fireEvent.click(await screen.findByTitle("2026-11-09"));
  confirm();
  await waitFor(() =>
    expect(screen.getByPlaceholderText("结束时间")).toHaveFocus(),
  );
  fireEvent.click(screen.getByText("最近 30 天"));
  expect(onChange).toHaveBeenCalledTimes(1);
  const [selected] = onChange.mock.calls[0]!;
  expect(Date.parse(selected.to) - Date.parse(selected.from)).toBe(
    30 * 86400000,
  );
});
