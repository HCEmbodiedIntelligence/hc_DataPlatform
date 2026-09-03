// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { adaptStorageOverview } from "../../../features/storage-overview/api/adapter";
import { storageOverviewFixture } from "../../../mocks/fixtures/storage-overview";
import { StorageSummaryStrip } from "./StorageSummaryStrip";

afterEach(cleanup);

describe("StorageSummaryStrip", () => {
  it("shows project storage by backend data stage without billing metrics", () => {
    render(
      <StorageSummaryStrip
        overview={adaptStorageOverview(storageOverviewFixture)}
        state="ready"
      />,
    );

    expect(
      within(screen.getByLabelText("当前项目总储量")).getByText("2 GiB"),
    ).toBeVisible();
    expect(
      within(screen.getByLabelText("原始 MCAP 容量")).getByText("1 GiB"),
    ).toBeVisible();
    expect(
      within(screen.getByLabelText("Lance 加工数据容量")).getByText("512 MiB"),
    ).toBeVisible();
    expect(screen.queryByText("计费容量")).not.toBeInTheDocument();
    expect(screen.queryByText("业务数据总量")).not.toBeInTheDocument();
    expect(screen.queryByText("共享存储比例")).not.toBeInTheDocument();
    expect(screen.queryByText("33.3%")).not.toBeInTheDocument();
    expect(screen.queryByText("月度费用")).not.toBeInTheDocument();
    expect(screen.queryByText(/人民币/u)).not.toBeInTheDocument();
  });
});
