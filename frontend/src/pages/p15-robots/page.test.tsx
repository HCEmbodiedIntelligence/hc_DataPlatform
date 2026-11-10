// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { Component } from "./page";

function Destination() {
  const { pathname, search, hash } = useLocation();
  return (
    <output>
      {pathname}
      {search}
      {hash}
    </output>
  );
}
afterEach(cleanup);
it.each([
  [
    "/settings/robots?modelId=m&q=arm#viewer",
    "/settings/robot-models?modelId=m&q=arm#viewer",
  ],
  ["/settings/robots?robotId=r", "/settings/robot-instances?robot=r"],
])("resolves the saved link %s", (from, to) => {
  render(
    <MemoryRouter initialEntries={[from]}>
      <Routes>
        <Route path="/settings/robots" element={<Component />} />
        <Route path="*" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByRole("status").textContent).toBe(to);
});
