// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import {
  AnnotationRevisionUnavailableRoute,
  LegacyAnnotationIndexRedirect,
  LegacyCleaningDraftsRedirect,
  LegacyCleaningWorkbenchRedirect,
  LegacyUploadIndexRedirect,
} from "./RouteCompatibility";

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  );
}

afterEach(cleanup);

function renderRedirect(
  path: string,
  pattern: string,
  element: React.ReactNode,
) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={pattern} element={element} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("legacy route compatibility", () => {
  it("moves P11 into annotation revision mode and preserves project, region, query and hash", async () => {
    renderRedirect(
      "/manual/drafts/draft-42?project=prj-7&region=cn-east&q=%E5%BE%85%E5%AE%A1&state=OPEN#step-9",
      "/manual/drafts/:draftId",
      <LegacyCleaningWorkbenchRedirect />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/annotations/revisions?project=prj-7&region=cn-east&q=%E5%BE%85%E5%AE%A1&state=OPEN&legacyDraftId=draft-42#step-9",
      ),
    );
  });

  it("moves the legacy draft list into the same revision mode", async () => {
    renderRedirect(
      "/manual/drafts?project=prj-7&region=cn-east",
      "/manual/drafts",
      <LegacyCleaningDraftsRedirect />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/annotations/revisions?project=prj-7&region=cn-east",
      ),
    );
  });

  it("normalizes legacy upload and annotation indexes to explicit page modes", async () => {
    renderRedirect(
      "/ingest/uploads?tab=failed&q=robot#results",
      "/ingest/uploads",
      <LegacyUploadIndexRedirect />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/ingest/uploads/records?tab=failed&q=robot#results",
      ),
    );
    cleanup();

    renderRedirect(
      "/annotations?queue=claimable&project=prj-7",
      "/annotations",
      <LegacyAnnotationIndexRedirect />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/annotations/annotate?queue=claimable&project=prj-7",
      ),
    );
  });
});

describe("unimplemented target modes", () => {
  it("shows honest feature-unavailable states", () => {
    render(<AnnotationRevisionUnavailableRoute />);
    expect(
      screen.getByText(/历史清洗入口已迁移到数据标注的数据修订模式/),
    ).toBeVisible();
    expect(screen.getByText("能力尚未开放")).toBeVisible();
  });
});
