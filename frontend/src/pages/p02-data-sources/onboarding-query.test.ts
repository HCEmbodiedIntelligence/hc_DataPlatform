import { expect, it } from "vitest";
import { parseRobotFilters, updateRobotFilters } from "./onboarding-query";
import { runtimeOperations } from "../../shared/api/generated/platform-operations";
it("keeps external tab conditions and restores robot conditions across pagination and refresh", () => {
  const initial = new URLSearchParams(
    "tab=robots&sourceType=OSS_IMPORT&q=external&robot.permission=DISABLED&robot.credential=AVAILABLE&robot.page=3",
  );
  const next = updateRobotFilters(initial, { connection: "OFFLINE" });
  expect(parseRobotFilters(new URLSearchParams(next.toString()))).toMatchObject(
    {
      page: 1,
      permission: "DISABLED",
      credential: "AVAILABLE",
      connection: "OFFLINE",
    },
  );
  expect(next.get("q")).toBe("external");
  expect(next.get("sourceType")).toBe("OSS_IMPORT");
  expect(parseRobotFilters(updateRobotFilters(next, { page: 2 })).page).toBe(2);
  expect(
    parseRobotFilters(updateRobotFilters(initial, { sort: "name" })).page,
  ).toBe(1);
  expect(
    parseRobotFilters(updateRobotFilters(initial, { page_size: 50 })).page,
  ).toBe(1);
});
it("rejects invalid URL filters without leaking them into requests", () => {
  expect(
    parseRobotFilters(
      new URLSearchParams(
        "robot.page=-2&robot.page_size=100&robot.connection=AUTH_FAILED&robot.permission=ADMIN",
      ),
    ),
  ).toMatchObject({ page: 1, page_size: 20, connection: "", permission: "" });
});
it("ships every onboarding request in the generated production operation gate", () => {
  for (const [method, path] of [
    ["GET", "/projects/{project_id}/robot-ingest/onboarding"],
    ["POST", "/projects/{project_id}/robot-ingest/onboarding"],
    ["GET", "/projects/{project_id}/robot-ingest/successful-volume"],
    ["POST", "/robot-ingest/heartbeat"],
    [
      "GET",
      "/projects/{project_id}/regions/{region_code}/lerobot-imports/robot-options",
    ],
  ])
    expect(
      runtimeOperations.some(
        (operation) => operation.path === path && operation.method === method,
      ),
    ).toBe(true);
});
